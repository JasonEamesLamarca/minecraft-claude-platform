package dev.mcai.mod;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.BiConsumer;
import net.minecraft.block.Block;
import net.minecraft.block.BlockState;
import net.minecraft.command.argument.BlockArgumentParser;
import net.minecraft.particle.ParticleTypes;
import net.minecraft.registry.Registries;
import net.minecraft.registry.RegistryKey;
import net.minecraft.registry.RegistryKeys;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.structure.StructurePlacementData;
import net.minecraft.structure.StructureTemplate;
import net.minecraft.structure.StructureTemplateManager;
import net.minecraft.util.Clearable;
import net.minecraft.util.Identifier;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Vec3i;
import net.minecraft.util.WorldSavePath;

/**
 * Places approved build plans through the game's own API, a limited number of blocks per tick, after saving a
 * vanilla structure snapshot of the area (world/generated/mcai/structures/undo/<id>.nbt) that /ai undo restores.
 * Block strings are parsed with the game's block parser with NBT disabled; nothing is ever run as a command.
 * Runs on the server thread only.
 */
public final class BuildExecutor {
    /** Callback for reports to the gateway: (request id, report JSON). */
    private final BiConsumer<String, JsonObject> reporter;
    private final int blocksPerTick;
    private final Deque<Job> queue = new ArrayDeque<>();
    private final List<Outline> outlines = new ArrayList<>();

    private record Job(String id, ServerWorld world, PlanCursor cursor, Map<String, BlockState> states) {
    }

    private record Outline(ServerPlayerEntity viewer, ServerWorld world, int[] from, int[] to, long untilTick) {
    }

    public BuildExecutor(BiConsumer<String, JsonObject> reporter, int blocksPerTick) {
        this.reporter = reporter;
        this.blocksPerTick = blocksPerTick;
    }

    static Identifier snapshotId(String requestId) {
        return Identifier.of("mcai", "undo/" + requestId);
    }

    static ServerWorld world(MinecraftServer server, String dimension) {
        Identifier id = Identifier.tryParse(dimension);
        return id == null ? null : server.getWorld(RegistryKey.of(RegistryKeys.WORLD, id));
    }

    /** Parse block strings with NBT disabled. Returns the first invalid one, or null if all are valid. */
    static String parseAll(Iterable<String> blocks, Map<String, BlockState> out) {
        for (String b : blocks) {
            try {
                out.put(b, BlockArgumentParser.block(Registries.BLOCK.getReadOnlyWrapper(), b, false).blockState());
            } catch (Exception e) {
                return b;
            }
        }
        out.put(PlanCursor.AIR, net.minecraft.block.Blocks.AIR.getDefaultState());
        return null;
    }

    static int[] ints(JsonElement e) {
        JsonArray a = e.getAsJsonArray();
        return new int[] {a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt()};
    }

    /** Preview: check that every block string is valid for this game version; show an outline for 30 s. */
    public String preview(MinecraftServer server, JsonObject event, ServerPlayerEntity viewer) {
        List<String> blocks = new ArrayList<>();
        event.getAsJsonArray("blocks").forEach(b -> blocks.add(b.getAsString()));
        String bad = parseAll(blocks, new HashMap<>());
        if (bad != null) {
            return bad;
        }
        ServerWorld world = world(server, event.get("dimension").getAsString());
        if (viewer != null && world != null && viewer.getServerWorld() == world) {
            JsonObject s = event.getAsJsonObject("summary");
            outlines.add(new Outline(viewer, world, ints(s.get("from")), ints(s.get("to")), server.getTicks() + 600L));
        }
        return null;
    }

    /** Execute an approved plan: snapshot the area, then queue the blocks. */
    public void execute(MinecraftServer server, JsonObject event) {
        String id = event.get("request_id").getAsString();
        JsonObject plan = event.getAsJsonObject("plan");
        JsonObject summary = event.getAsJsonObject("summary");
        try {
            ServerWorld world = world(server, plan.get("dimension").getAsString());
            if (world == null) {
                fail(id, "unknown dimension", 0);
                return;
            }
            List<PlanCursor.Action> actions = new ArrayList<>();
            List<String> blocks = new ArrayList<>();
            for (JsonElement el : plan.getAsJsonArray("actions")) {
                JsonObject a = el.getAsJsonObject();
                String block = a.get("block").getAsString();
                blocks.add(block);
                if ("setblock".equals(a.get("op").getAsString())) {
                    int[] p = ints(a.get("pos"));
                    actions.add(new PlanCursor.Action(p, p, block, "replace"));
                } else {
                    actions.add(new PlanCursor.Action(ints(a.get("from")), ints(a.get("to")), block,
                            a.get("mode").getAsString()));
                }
            }
            Map<String, BlockState> states = new HashMap<>();
            String bad = parseAll(blocks, states);
            if (bad != null) {
                fail(id, "invalid block " + bad, 0);
                return;
            }
            int[] from = ints(summary.get("from"));
            int[] to = ints(summary.get("to"));
            StructureTemplateManager mgr = server.getStructureTemplateManager();
            StructureTemplate snap = mgr.getTemplateOrBlank(snapshotId(id));
            snap.saveFromWorld(world, new BlockPos(from[0], from[1], from[2]),
                    new Vec3i(to[0] - from[0] + 1, to[1] - from[1] + 1, to[2] - from[2] + 1), false, null);
            if (!mgr.saveTemplate(snapshotId(id))) {
                fail(id, "could not save the undo snapshot", 0);
                return;
            }
            queue.add(new Job(id, world, new PlanCursor(actions), states));
            McaiMod.LOG.info("[mcai] build {}: snapshot saved, {} blocks queued", id, summary.get("block_count").getAsInt());
        } catch (Exception e) {
            McaiMod.LOG.error("[mcai] build {} failed to start", id, e);
            fail(id, "could not start", 0);
        }
    }

    /** Undo: place the snapshot back. */
    public void rollback(MinecraftServer server, JsonObject event) {
        String id = event.get("request_id").getAsString();
        JsonObject report = new JsonObject();
        try {
            ServerWorld world = world(server, event.get("dimension").getAsString());
            StructureTemplate snap = server.getStructureTemplateManager().getTemplate(snapshotId(id)).orElse(null);
            if (world == null || snap == null) {
                report.addProperty("status", "rollback_failed");
                report.addProperty("error", "snapshot not found");
            } else {
                queue.removeIf(j -> j.id().equals(id));     // stop the build if it is still running
                int[] from = ints(event.getAsJsonObject("summary").get("from"));
                BlockPos pos = new BlockPos(from[0], from[1], from[2]);
                snap.place(world, pos, pos, new StructurePlacementData(), world.getRandom(), Block.NOTIFY_LISTENERS);
                report.addProperty("status", "rolled_back");
                McaiMod.LOG.info("[mcai] build {} rolled back", id);
            }
        } catch (Exception e) {
            McaiMod.LOG.error("[mcai] rollback {} failed", id, e);
            report.addProperty("status", "rollback_failed");
            report.addProperty("error", "error while restoring");
        }
        reporter.accept(id, report);
    }

    /** Delete undo snapshots the gateway no longer needs (undone builds, or older than the undo window). */
    public void prune(MinecraftServer server, JsonObject event) {
        StructureTemplateManager mgr = server.getStructureTemplateManager();
        java.nio.file.Path dir = server.getSavePath(WorldSavePath.GENERATED).resolve("mcai").resolve("structures")
                .resolve("undo");
        for (JsonElement el : event.getAsJsonArray("request_ids")) {
            String id = el.getAsString();
            if (!id.matches("[0-9a-f]{6,12}")) {        // ids become file names: never accept anything else
                continue;
            }
            mgr.unloadTemplate(snapshotId(id));
            try {
                java.nio.file.Files.deleteIfExists(dir.resolve(id + ".nbt"));
                JsonObject report = new JsonObject();
                report.addProperty("status", "pruned");
                reporter.accept(id, report);
            } catch (java.io.IOException e) {
                McaiMod.LOG.warn("[mcai] could not delete undo snapshot {}: {}", id, e.toString());
            }
        }
    }

    /** Called every server tick. */
    public void tick(MinecraftServer server) {
        drawOutlines(server);
        Job job = queue.peek();
        if (job == null) {
            return;
        }
        try {
            for (int i = 0; i < blocksPerTick; i++) {
                PlanCursor.Step s = job.cursor().next();
                if (s == null) {
                    queue.poll();
                    JsonObject report = new JsonObject();
                    report.addProperty("status", "built");
                    report.addProperty("placed", job.cursor().emitted());
                    reporter.accept(job.id(), report);
                    McaiMod.LOG.info("[mcai] build {} done: {} blocks", job.id(), job.cursor().emitted());
                    return;
                }
                BlockPos pos = new BlockPos(s.x(), s.y(), s.z());
                // Like /fill: empty containers first, so replaced blocks don't drop items (undo restores them).
                Clearable.clear(job.world().getBlockEntity(pos));
                job.world().setBlockState(pos, job.states().get(s.block()), Block.NOTIFY_LISTENERS);
            }
        } catch (Exception e) {
            queue.poll();
            McaiMod.LOG.error("[mcai] build {} failed", job.id(), e);
            fail(job.id(), "error while placing blocks", job.cursor().emitted());
        }
    }

    private void fail(String id, String error, long placed) {
        JsonObject report = new JsonObject();
        report.addProperty("status", "failed");
        report.addProperty("error", error);
        report.addProperty("placed", placed);
        reporter.accept(id, report);
    }

    private void drawOutlines(MinecraftServer server) {
        long now = server.getTicks();
        outlines.removeIf(o -> o.untilTick() < now || o.viewer().isRemoved());
        if (now % 20 != 0) {
            return;
        }
        for (Outline o : outlines) {
            int[] a = o.from();
            int[] b = {o.to()[0] + 1, o.to()[1] + 1, o.to()[2] + 1};
            for (int i = 0; i < 12; i++) {           // the 12 edges of the box
                int axis = i / 4;
                double[] p = {a[0], a[1], a[2]};
                int[] other = axis == 0 ? new int[] {1, 2} : axis == 1 ? new int[] {0, 2} : new int[] {0, 1};
                p[other[0]] = (i & 1) == 0 ? a[other[0]] : b[other[0]];
                p[other[1]] = (i & 2) == 0 ? a[other[1]] : b[other[1]];
                for (int t = a[axis]; t <= b[axis]; t++) {
                    p[axis] = t;
                    o.world().spawnParticles(o.viewer(), ParticleTypes.END_ROD, true, p[0], p[1], p[2], 1, 0, 0, 0, 0);
                }
            }
        }
    }
}
