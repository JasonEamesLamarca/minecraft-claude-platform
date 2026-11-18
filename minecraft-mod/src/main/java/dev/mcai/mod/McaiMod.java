package dev.mcai.mod;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.builder.LiteralArgumentBuilder;
import com.mojang.brigadier.context.CommandContext;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Pattern;
import net.fabricmc.api.DedicatedServerModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.minecraft.advancement.AdvancementEntry;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.command.CommandManager;
import net.minecraft.server.command.ServerCommandSource;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.text.MutableText;
import net.minecraft.text.Text;
import net.minecraft.util.Formatting;
import net.minecraft.util.math.BlockPos;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Server-side entrypoint. Registers the /ai command (name configurable), relays gateway messages to players,
 * shows build previews and executes approved builds, and reports joins/leaves, server health and the game
 * events automations listen for (areas, advancements, /ai trigger). Authorization is decided by the gateway from
 * the player's UUID and role; the mod only enforces a minimum vanilla permission level for the command itself.
 */
public final class McaiMod implements DedicatedServerModInitializer {
    static final Logger LOG = LoggerFactory.getLogger("mcai");
    static final UUID CONSOLE = new UUID(0L, 0L);
    private static final String ID_ARG = "id";
    private static final Pattern TRIGGER = Pattern.compile("[a-z0-9_-]{1,32}");
    private static volatile McaiMod instance;

    private McaiConfig config;
    private GatewayClient gateway;
    private BuildExecutor builds;
    private ExamplePlacer examples;
    private volatile MinecraftServer server;
    private final Map<UUID, String> online = new HashMap<>();
    private final AreaWatcher areas = new AreaWatcher();
    private long startedAt;

    @Override
    public void onInitializeServer() {
        try {
            config = McaiConfig.fromEnvironment(System.getenv());
        } catch (Exception e) {
            LOG.error("[mcai] disabled: {}", e.getMessage());
            return;
        }
        gateway = new GatewayClient(config);
        instance = this;
        builds = new BuildExecutor((id, report) -> gateway.post("/v1/mod/builds/" + id + "/report", report),
                Integer.parseInt(System.getenv().getOrDefault("MCAI_BLOCKS_PER_TICK", "2000")));
        examples = new ExamplePlacer((id, report) -> gateway.post("/v1/mod/examples/" + id + "/report", report),
                path -> gateway.getBytes(path, ExamplePlacer.MAX_FILE_BYTES));
        CommandRegistrationCallback.EVENT.register((dispatcher, registries, env) -> register(dispatcher));
        ServerLifecycleEvents.SERVER_STARTED.register(s -> {
            server = s;
            startedAt = System.currentTimeMillis();
            gateway.start(this::deliver);
            LOG.info("[mcai] ready: /{} -> {}", config.commandName(), config.gatewayUrl());
        });
        ServerLifecycleEvents.SERVER_STOPPING.register(s -> gateway.stop());
        ServerTickEvents.END_SERVER_TICK.register(s -> {
            builds.tick(s);
            examples.tick(s);
            if (s.getTicks() % 20 == 0) {
                trackPlayers(s);
            }
            if (s.getTicks() % 10 == 5 && areas.size() > 0) {
                checkAreas(s);
            }
            if (s.getTicks() % 600 == 100) {
                heartbeat(s);
            }
        });
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
                        .executes(c -> request(c, "build"))))
                .then(CommandManager.literal("link").then(CommandManager.argument("code", StringArgumentType.word())
                        .executes(this::link)))
                .then(CommandManager.literal("trigger").then(CommandManager.argument("name", StringArgumentType.word())
                        .executes(this::trigger)));
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
                + c + " cancel [id] | " + c + " undo [id] | " + c + " status | " + c + " history | " + c + " link <code> | "
                + c + " trigger <name>",
                false);
        return 1;
    }

    /** Identity and position of the caller (the gateway looks up the role by UUID). Console: world spawn. */
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
            ctx.addProperty("dimension", "minecraft:overworld");
            ctx.addProperty("x", spawn.getX());
            ctx.addProperty("y", spawn.getY());
            ctx.addProperty("z", spawn.getZ());
            ctx.addProperty("yaw", 0);
        } else {
            o.addProperty("player_uuid", p.getUuid().toString());
            o.addProperty("player_name", p.getName().getString());
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

    private int link(CommandContext<ServerCommandSource> ctx) {
        ServerCommandSource src = ctx.getSource();
        JsonObject body = caller(src);
        if (body == null || src.getPlayer() == null) {
            src.sendError(Text.literal("Only players can link an account."));
            return 0;
        }
        body.remove("context");
        body.addProperty("code", StringArgumentType.getString(ctx, "code").toLowerCase());
        gateway.post("/v1/mod/link", body).thenAccept(r -> runOnServer(() -> {
            boolean ok = r.has("ok") && r.get("ok").getAsBoolean();
            say(src, r.has("message") ? r.get("message").getAsString() : (ok ? "Linked." : "Failed."), !ok);
        }));
        return 1;
    }

    /** /ai trigger NAME: start an automation the server offers (the gateway checks who may). */
    private int trigger(CommandContext<ServerCommandSource> ctx) {
        ServerCommandSource src = ctx.getSource();
        ServerPlayerEntity p = src.getPlayer();
        String name = StringArgumentType.getString(ctx, "name").toLowerCase();
        if (p == null) {
            src.sendError(Text.literal("Only players can start triggers."));
            return 0;
        }
        if (!TRIGGER.matcher(name).matches()) {
            src.sendError(Text.literal("Trigger names are lowercase letters, digits, - and _."));
            return 0;
        }
        JsonObject o = gameEvent("trigger", p.getUuid(), p.getName().getString());
        o.addProperty("name", name);
        gateway.post("/v1/mod/game-events", o).thenAccept(r -> runOnServer(() -> {
            boolean ok = r.has("ok") && r.get("ok").getAsBoolean();
            say(src, r.has("message") ? r.get("message").getAsString() : (ok ? "Started." : "Failed."), !ok);
        }));
        return 1;
    }

    private static JsonObject gameEvent(String type, UUID player, String name) {
        JsonObject o = new JsonObject();
        o.addProperty("type", type);
        o.addProperty("event_id", UUID.randomUUID().toString());
        o.addProperty("player_uuid", player.toString());
        o.addProperty("player_name", name);
        return o;
    }

    /** Called by the advancement mixin when a player completes an advancement (not recipes: they have no display). */
    public static void onAdvancement(ServerPlayerEntity player, AdvancementEntry entry) {
        McaiMod mod = instance;
        if (mod == null || mod.gateway == null || player == null || entry.value().display().isEmpty()) {
            return;
        }
        JsonObject o = gameEvent("advancement", player.getUuid(), player.getName().getString());
        o.addProperty("advancement", entry.id().toString());
        o.addProperty("title", entry.value().display().get().getTitle().getString());
        mod.gateway.post("/v1/mod/game-events", o);
    }

    /** Players entering/leaving the areas of enabled area automations (checked twice a second). */
    private void checkAreas(MinecraftServer s) {
        List<AreaWatcher.Pos> positions = new ArrayList<>();
        for (ServerPlayerEntity p : s.getPlayerManager().getPlayerList()) {
            positions.add(new AreaWatcher.Pos(p.getUuid(), p.getName().getString(),
                    p.getServerWorld().getRegistryKey().getValue().toString(), (int) Math.floor(p.getX()),
                    (int) Math.floor(p.getY()), (int) Math.floor(p.getZ())));
        }
        for (AreaWatcher.Crossing c : areas.check(positions)) {
            JsonObject o = gameEvent(c.enter() ? "area_enter" : "area_leave", c.player(), c.name());
            o.addProperty("area", c.areaId());
            gateway.post("/v1/mod/game-events", o);
        }
    }

    /** Join/leave events: compare the player list once a second (no extra Fabric API module needed). */
    private void trackPlayers(MinecraftServer s) {
        Map<UUID, String> now = new HashMap<>();
        for (ServerPlayerEntity p : s.getPlayerManager().getPlayerList()) {
            now.put(p.getUuid(), p.getName().getString());
        }
        for (Map.Entry<UUID, String> e : now.entrySet()) {
            if (!online.containsKey(e.getKey())) {
                playerEvent("join", e.getKey(), e.getValue());
            }
        }
        for (Map.Entry<UUID, String> e : online.entrySet()) {
            if (!now.containsKey(e.getKey())) {
                playerEvent("leave", e.getKey(), e.getValue());
            }
        }
        online.clear();
        online.putAll(now);
    }

    private void playerEvent(String event, UUID id, String name) {
        JsonObject o = new JsonObject();
        o.addProperty("event", event);
        o.addProperty("player_uuid", id.toString());
        o.addProperty("player_name", name);
        gateway.post("/v1/mod/players", o);
    }

    /** Server health for 'mcai status': tick time, heap, online players (also repairs missed join/leave events). */
    private void heartbeat(MinecraftServer s) {
        JsonObject o = new JsonObject();
        JsonArray players = new JsonArray();
        for (ServerPlayerEntity p : s.getPlayerManager().getPlayerList()) {
            JsonObject e = new JsonObject();
            e.addProperty("uuid", p.getUuid().toString());
            e.addProperty("name", p.getName().getString());
            players.add(e);
        }
        Runtime rt = Runtime.getRuntime();
        o.add("players", players);
        o.addProperty("mspt", s.getAverageTickTime());
        o.addProperty("heap_used_mb", (rt.totalMemory() - rt.freeMemory()) / 1048576L);
        o.addProperty("heap_max_mb", rt.maxMemory() / 1048576L);
        o.addProperty("uptime_s", (System.currentTimeMillis() - startedAt) / 1000L);
        o.addProperty("version", s.getVersion());
        ServerWorld overworld = s.getOverworld();
        JsonObject world = new JsonObject();
        world.addProperty("time", overworld.getTimeOfDay());
        world.addProperty("raining", overworld.isRaining());
        world.addProperty("thundering", overworld.isThundering());
        o.add("world", world);
        o.addProperty("example_job", examples.current());
        gateway.post("/v1/mod/heartbeat", o).thenAccept(r -> {
            if (r.has("areas")) {
                areas.setAreas(AreaWatcher.parse(r.get("areas")));
            }
        });
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
                    case "prune" -> builds.prune(server, event);
                    case "example_place" -> examples.place(server, event);
                    case "example_rollback" -> examples.rollback(server, event);
                    case "example_cancel" -> examples.cancel(event);
                    case "example_forget" -> examples.forget(server, event);
                    case "preview" -> preview(event);
                    case "broadcast" -> broadcast(event);
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

    /** A message from an automation to everyone online (text only, shown with the [AI] prefix). */
    private void broadcast(JsonObject event) {
        if (!event.has("text")) {
            return;
        }
        String clean = ChatText.clean(event.get("text").getAsString());
        LOG.info("[AI] (broadcast) {}", clean);
        server.getPlayerManager().broadcast(prefix().append(Text.literal(clean).formatted(Formatting.AQUA)), false);
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
