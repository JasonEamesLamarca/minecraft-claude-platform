package dev.mcai.mod;

import com.google.gson.JsonObject;
import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.builder.LiteralArgumentBuilder;
import com.mojang.brigadier.context.CommandContext;
import java.util.UUID;
import net.fabricmc.api.DedicatedServerModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.command.CommandManager;
import net.minecraft.server.command.ServerCommandSource;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.text.MutableText;
import net.minecraft.text.Text;
import net.minecraft.util.Formatting;
import net.minecraft.util.math.BlockPos;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Server-side entrypoint. Registers the /ai command (name configurable), relays gateway messages to players,
 * shows build previews and executes approved builds. Authorization is decided by the gateway (authoritative);
 * the mod reports the caller's vanilla permission level and enforces a minimum level for the command itself.
 */
public final class McaiMod implements DedicatedServerModInitializer {
    static final Logger LOG = LoggerFactory.getLogger("mcai");
    static final UUID CONSOLE = new UUID(0L, 0L);
    private static final String ID_ARG = "id";

    private McaiConfig config;
    private GatewayClient gateway;
    private BuildExecutor builds;
    private volatile MinecraftServer server;

    @Override
    public void onInitializeServer() {
        try {
            config = McaiConfig.fromEnvironment(System.getenv());
        } catch (Exception e) {
            LOG.error("[mcai] disabled: {}", e.getMessage());
            return;
        }
        gateway = new GatewayClient(config);
        builds = new BuildExecutor((id, report) -> gateway.post("/v1/mod/builds/" + id + "/report", report),
                Integer.parseInt(System.getenv().getOrDefault("MCAI_BLOCKS_PER_TICK", "2000")));
        CommandRegistrationCallback.EVENT.register((dispatcher, registries, env) -> register(dispatcher));
        ServerLifecycleEvents.SERVER_STARTED.register(s -> {
            server = s;
            gateway.start(this::deliver);
            LOG.info("[mcai] ready: /{} -> {}", config.commandName(), config.gatewayUrl());
        });
        ServerLifecycleEvents.SERVER_STOPPING.register(s -> gateway.stop());
        ServerTickEvents.END_SERVER_TICK.register(s -> builds.tick(s));
    }

    private void register(CommandDispatcher<ServerCommandSource> dispatcher) {
        String name = config.commandName();
        if (dispatcher.getRoot().getChild(name) != null) {
            LOG.error("[mcai] /{} is already registered by another mod or datapack; set MCAI_COMMAND to another name", name);
            return;
        }
        LiteralArgumentBuilder<ServerCommandSource> root = CommandManager.literal(name)
                .requires(src -> src.hasPermissionLevel(config.askPermissionLevel()))
                .executes(this::help)
                .then(CommandManager.literal("help").executes(this::help))
                .then(CommandManager.literal("status").executes(c -> info(c, "/v1/mod/status")))
                .then(CommandManager.literal("history").executes(c -> info(c, "/v1/mod/history")))
                .then(CommandManager.literal("ask").then(CommandManager.argument("text", StringArgumentType.greedyString())
                        .executes(c -> request(c, "ask"))))
                .then(CommandManager.literal("build").then(CommandManager.argument("text", StringArgumentType.greedyString())
                        .executes(c -> request(c, "build"))));
        for (String action : new String[] {"approve", "cancel", "undo"}) {
            root.then(CommandManager.literal(action)
                    .executes(c -> decide(c, action, null))
                    .then(CommandManager.argument(ID_ARG, StringArgumentType.word())
                            .executes(c -> decide(c, action, StringArgumentType.getString(c, ID_ARG)))));
        }
        dispatcher.register(root);
    }

    private static MutableText prefix() {
        return Text.literal("[AI] ").formatted(Formatting.DARK_AQUA);
    }

    private static void say(ServerCommandSource src, String text, boolean error) {
        src.sendFeedback(() -> prefix().append(Text.literal(ChatText.clean(text))
                .formatted(error ? Formatting.RED : Formatting.GRAY)), false);
    }

    private int help(CommandContext<ServerCommandSource> ctx) {
        String c = "/" + config.commandName();
        say(ctx.getSource(), c + " ask <question> | " + c + " build <description> | " + c + " approve [id] | "
                + c + " cancel [id] | " + c + " undo [id] | " + c + " status | " + c + " history", false);
        return 1;
    }

    /** Identity, vanilla permission level and position of the caller. Console: level 4, world spawn. */
    private JsonObject caller(ServerCommandSource src) {
        JsonObject o = new JsonObject();
        JsonObject ctx = new JsonObject();
        ServerPlayerEntity p = src.getPlayer();
        if (p == null) {
            if (!config.allowConsole()) {
                return null;
            }
            BlockPos spawn = src.getServer().getOverworld().getSpawnPos();
            o.addProperty("player_uuid", CONSOLE.toString());
            o.addProperty("player_name", "Server");
            o.addProperty("op_level", 4);
            ctx.addProperty("dimension", "minecraft:overworld");
            ctx.addProperty("x", spawn.getX());
            ctx.addProperty("y", spawn.getY());
            ctx.addProperty("z", spawn.getZ());
            ctx.addProperty("yaw", 0);
        } else {
            o.addProperty("player_uuid", p.getUuid().toString());
            o.addProperty("player_name", p.getName().getString());
            o.addProperty("op_level", src.getServer().getPermissionLevel(p.getGameProfile()));
            ctx.addProperty("dimension", p.getServerWorld().getRegistryKey().getValue().toString());
            ctx.addProperty("x", (int) Math.floor(p.getX()));
            ctx.addProperty("y", (int) Math.floor(p.getY()));
            ctx.addProperty("z", (int) Math.floor(p.getZ()));
            ctx.addProperty("yaw", Math.round(p.getYaw()));
            ctx.addProperty("game_mode", p.interactionManager.getGameMode().asString());
        }
        o.add("context", ctx);
        return o;
    }

    private int request(CommandContext<ServerCommandSource> ctx, String kind) {
        ServerCommandSource src = ctx.getSource();
        JsonObject body = caller(src);
        if (body == null) {
            src.sendError(Text.literal("This command can only be used by players."));
            return 0;
        }
        body.addProperty("kind", kind);
        body.addProperty("text", StringArgumentType.getString(ctx, "text"));
        say(src, "ask".equals(kind) ? "Thinking..." : "Sending your build request...", false);
        gateway.post("/v1/mod/requests", body).thenAccept(r -> runOnServer(() -> {
            boolean accepted = r.has("accepted") && r.get("accepted").getAsBoolean();
            String msg = r.has("message") ? r.get("message").getAsString() : "Request rejected.";
            if (!accepted) {
                src.sendError(Text.literal(ChatText.clean(msg)));
            } else if ("build".equals(kind)) {
                say(src, msg, false);
            }
        }));
        return 1;
    }

    private int decide(CommandContext<ServerCommandSource> ctx, String action, String id) {
        ServerCommandSource src = ctx.getSource();
        JsonObject body = caller(src);
        if (body == null) {
            src.sendError(Text.literal("This command can only be used by players."));
            return 0;
        }
        body.remove("context");
        if (id != null) {
            body.addProperty("request_id", id.toLowerCase());
        }
        gateway.post("/v1/mod/" + action, body).thenAccept(r -> runOnServer(() -> {
            boolean ok = r.has("ok") && r.get("ok").getAsBoolean();
            say(src, r.has("message") ? r.get("message").getAsString() : (ok ? "Done." : "Failed."), !ok);
        }));
        return 1;
    }

    private int info(CommandContext<ServerCommandSource> ctx, String path) {
        ServerCommandSource src = ctx.getSource();
        JsonObject who = caller(src);
        if (who == null) {
            src.sendError(Text.literal("This command can only be used by players."));
            return 0;
        }
        gateway.get(path + "?player_uuid=" + who.get("player_uuid").getAsString())
                .thenAccept(r -> runOnServer(() -> say(src,
                        r.has("message") ? r.get("message").getAsString() : "No information available.", false)));
        return 1;
    }

    /** Handle one gateway event on the server thread. */
    private void deliver(JsonObject event) {
        runOnServer(() -> {
            try {
                String type = event.has("type") ? event.get("type").getAsString() : "message";
                switch (type) {
                    case "execute" -> builds.execute(server, event);
                    case "rollback" -> builds.rollback(server, event);
                    case "preview" -> preview(event);
                    default -> message(event);
                }
            } catch (RuntimeException e) {
                LOG.warn("[mcai] could not handle gateway event: {}", e.toString());
            }
        });
    }

    private UUID target(JsonObject event) {
        try {
            return UUID.fromString(event.get("player_uuid").getAsString());
        } catch (RuntimeException e) {
            return null;
        }
    }

    private void tell(UUID target, String text, boolean error) {
        String clean = ChatText.clean(text);
        if (CONSOLE.equals(target)) {
            LOG.info("[AI] {}", clean);
            return;
        }
        ServerPlayerEntity p = server.getPlayerManager().getPlayer(target);
        if (p != null) {
            p.sendMessage(prefix().append(Text.literal(clean).formatted(error ? Formatting.RED : Formatting.AQUA)), false);
        }
    }

    private void message(JsonObject event) {
        UUID target = target(event);
        if (target != null && event.has("text")) {
            tell(target, event.get("text").getAsString(), event.has("level") && "error".equals(event.get("level").getAsString()));
        }
    }

    private void preview(JsonObject event) {
        UUID target = target(event);
        if (target == null) {
            return;
        }
        ServerPlayerEntity viewer = CONSOLE.equals(target) ? null : server.getPlayerManager().getPlayer(target);
        String bad = builds.preview(server, event, viewer);
        String id = event.get("request_id").getAsString();
        if (bad != null) {
            JsonObject report = new JsonObject();
            report.addProperty("status", "invalid");
            report.addProperty("error", bad);
            gateway.post("/v1/mod/builds/" + id + "/report", report);
            return;
        }
        tell(target, event.get("text").getAsString(), false);
    }

    private void runOnServer(Runnable r) {
        MinecraftServer s = server;
        if (s != null) {
            s.execute(r);
        }
    }
}
