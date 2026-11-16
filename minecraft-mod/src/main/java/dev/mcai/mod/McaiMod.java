package dev.mcai.mod;

import com.google.gson.JsonObject;
import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.context.CommandContext;
import java.util.UUID;
import net.fabricmc.api.DedicatedServerModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.command.CommandManager;
import net.minecraft.server.command.ServerCommandSource;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.text.MutableText;
import net.minecraft.text.Text;
import net.minecraft.util.Formatting;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Server-side entrypoint. Registers the /ai command (name configurable) and relays gateway replies to players.
 * Authorization is enforced twice: a vanilla permission level here, and the platform's own checks in the gateway,
 * which are authoritative.
 */
public final class McaiMod implements DedicatedServerModInitializer {
    static final Logger LOG = LoggerFactory.getLogger("mcai");
    static final UUID CONSOLE = new UUID(0L, 0L);

    private McaiConfig config;
    private GatewayClient gateway;
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
        CommandRegistrationCallback.EVENT.register((dispatcher, registries, env) -> register(dispatcher));
        ServerLifecycleEvents.SERVER_STARTED.register(s -> {
            server = s;
            gateway.start(this::deliver);
            LOG.info("[mcai] ready: /{} -> {}", config.commandName(), config.gatewayUrl());
        });
        ServerLifecycleEvents.SERVER_STOPPING.register(s -> gateway.stop());
    }

    private void register(CommandDispatcher<ServerCommandSource> dispatcher) {
        String name = config.commandName();
        if (dispatcher.getRoot().getChild(name) != null) {
            LOG.error("[mcai] /{} is already registered by another mod or datapack; set MCAI_COMMAND to another name", name);
            return;
        }
        dispatcher.register(CommandManager.literal(name)
                .requires(src -> src.hasPermissionLevel(config.askPermissionLevel()))
                .executes(this::help)
                .then(CommandManager.literal("help").executes(this::help))
                .then(CommandManager.literal("status").executes(this::status))
                .then(CommandManager.literal("ask")
                        .then(CommandManager.argument("question", StringArgumentType.greedyString())
                                .executes(this::ask))));
    }

    private static MutableText prefix() {
        return Text.literal("[AI] ").formatted(Formatting.DARK_AQUA);
    }

    private int help(CommandContext<ServerCommandSource> ctx) {
        String c = "/" + config.commandName();
        ctx.getSource().sendFeedback(() -> prefix().append(Text.literal(
                c + " ask <question> - ask the AI assistant | " + c + " status - your requests and today's budget | "
                        + c + " help").formatted(Formatting.GRAY)), false);
        return 1;
    }

    /** Identity + context of the caller. Console is allowed only when MCAI_ALLOW_CONSOLE=true. */
    private JsonObject caller(ServerCommandSource src) {
        JsonObject o = new JsonObject();
        ServerPlayerEntity p = src.getPlayer();
        if (p == null) {
            if (!config.allowConsole()) {
                return null;
            }
            o.addProperty("player_uuid", CONSOLE.toString());
            o.addProperty("player_name", "Server");
            return o;
        }
        o.addProperty("player_uuid", p.getUuid().toString());
        o.addProperty("player_name", p.getName().getString());
        JsonObject ctx = new JsonObject();
        ctx.addProperty("dimension", p.getServerWorld().getRegistryKey().getValue().toString());
        ctx.addProperty("x", (int) Math.floor(p.getX()));
        ctx.addProperty("y", (int) Math.floor(p.getY()));
        ctx.addProperty("z", (int) Math.floor(p.getZ()));
        ctx.addProperty("yaw", Math.round(p.getYaw()));
        ctx.addProperty("game_mode", p.interactionManager.getGameMode().asString());
        o.add("context", ctx);
        return o;
    }

    private int ask(CommandContext<ServerCommandSource> ctx) {
        ServerCommandSource src = ctx.getSource();
        JsonObject body = caller(src);
        if (body == null) {
            src.sendError(Text.literal("This command can only be used by players."));
            return 0;
        }
        body.addProperty("kind", "ask");
        body.addProperty("text", StringArgumentType.getString(ctx, "question"));
        src.sendFeedback(() -> prefix().append(Text.literal("Thinking...").formatted(Formatting.GRAY)), false);
        gateway.post("/v1/mod/requests", body).thenAccept(r -> runOnServer(() -> {
            boolean accepted = r.has("accepted") && r.get("accepted").getAsBoolean();
            if (!accepted) {
                String msg = r.has("message") ? r.get("message").getAsString() : "Request rejected.";
                src.sendError(Text.literal(ChatText.clean(msg)));
            }
        }));
        return 1;
    }

    private int status(CommandContext<ServerCommandSource> ctx) {
        ServerCommandSource src = ctx.getSource();
        JsonObject who = caller(src);
        if (who == null) {
            src.sendError(Text.literal("This command can only be used by players."));
            return 0;
        }
        gateway.get("/v1/mod/status?player_uuid=" + who.get("player_uuid").getAsString())
                .thenAccept(r -> runOnServer(() -> {
                    String msg = r.has("message") ? r.get("message").getAsString() : "No status available.";
                    src.sendFeedback(() -> prefix().append(Text.literal(ChatText.clean(msg)).formatted(Formatting.GRAY)), false);
                }));
        return 1;
    }

    /** Deliver one gateway event (on the server thread). */
    private void deliver(JsonObject event) {
        if (!event.has("player_uuid") || !event.has("text")) {
            return;
        }
        String text = ChatText.clean(event.get("text").getAsString());
        boolean error = event.has("level") && "error".equals(event.get("level").getAsString());
        UUID target;
        try {
            target = UUID.fromString(event.get("player_uuid").getAsString());
        } catch (IllegalArgumentException e) {
            return;
        }
        runOnServer(() -> {
            if (CONSOLE.equals(target)) {
                LOG.info("[AI] {}", text);
                return;
            }
            ServerPlayerEntity p = server.getPlayerManager().getPlayer(target);
            if (p != null) {
                p.sendMessage(prefix().append(Text.literal(text).formatted(error ? Formatting.RED : Formatting.AQUA)), false);
            }
        });
    }

    private void runOnServer(Runnable r) {
        MinecraftServer s = server;
        if (s != null) {
            s.execute(r);
        }
    }
}
