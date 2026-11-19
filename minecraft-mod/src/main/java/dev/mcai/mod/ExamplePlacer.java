package dev.mcai.mod;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.function.BiConsumer;
import java.util.function.Function;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;
import net.minecraft.block.Block;
import net.minecraft.block.BlockState;
import net.minecraft.command.argument.BlockArgumentParser;
import net.minecraft.nbt.NbtCompound;
import net.minecraft.nbt.NbtElement;
import net.minecraft.nbt.NbtIo;
import net.minecraft.nbt.NbtSizeTracker;
import net.minecraft.registry.Registries;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.world.ChunkTicketType;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.structure.StructurePlacementData;
import net.minecraft.structure.StructureTemplate;
import net.minecraft.structure.StructureTemplateManager;
import net.minecraft.util.Clearable;
import net.minecraft.util.Identifier;
import net.minecraft.util.WorldSavePath;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.ChunkPos;
import net.minecraft.util.math.Vec3i;

/**
 * Places example packs (Sponge v3 schematics, ADR-0012) sent by the gateway. Section files are downloaded from
 * the gateway, checked against their SHA-256, parsed with size and depth limits on a background thread, and every
 * palette entry is parsed with the game's block parser (NBT disabled) and checked against a deny-list.
 *
 * <p>On the server thread the section is worked through chunk by chunk ("pieces"). Before a piece is changed for
 * the first time its current content is saved as a vanilla structure snapshot
 * (world/generated/mcai/structures/examples/&lt;install&gt;/...). Only cells that differ from the pack are set, a
 * limited number per tick, so placing again repairs an install. Rollback places the snapshots back, one piece per
 * tick. Nothing is ever run as a command.
 */
public final class ExamplePlacer {
    /** Same list as the gateway's DENIED_PACK_BLOCKS. */
    static final Set<String> DENIED = Set.of("tnt", "command_block", "chain_command_block", "repeating_command_block",
            "structure_block", "jigsaw", "barrier", "bedrock", "lava", "fire", "soul_fire", "end_portal",
            "end_portal_frame", "end_gateway", "nether_portal", "spawner", "trial_spawner", "vault", "respawn_anchor",
            "wither_skeleton_skull", "wither_skeleton_wall_skull", "dragon_egg", "reinforced_deepslate",
            "budding_amethyst", "sculk_shrieker", "test_block", "test_instance_block");
    static final String SKIP = "minecraft:structure_void";
    static final int MAX_FILE_BYTES = 16 * 1024 * 1024;
    static final Pattern INSTALL_ID = Pattern.compile("[0-9a-f]{8}");
    static final Pattern FILE = Pattern.compile("[a-z0-9][a-z0-9-]{0,63}\\.schem");
    static final Pattern SNAPSHOT = Pattern.compile("s(\\d{1,3})_(m?\\d{1,8})_(m?\\d{1,3})_(m?\\d{1,8})");
    private static final int FLAGS = Block.NOTIFY_LISTENERS | Block.FORCE_STATE | Block.SKIP_DROPS;
    /**
     * Keeps the chunk being worked on loaded. Released as soon as its piece is done; the expiry (5 s) is only a
     * safety net. Holding chunks longer kept hundreds loaded during a rollback and exhausted a 1 GB heap.
     */
    private static final ChunkTicketType<ChunkPos> TICKET =
            ChunkTicketType.create("mcai_example", Comparator.comparingLong(ChunkPos::toLong), 100);

    record Section(String file, String sha256, int bytes, int[] offset, int[] size) {
    }

    /** A parsed section: palette states and one palette index per cell (x + z*w + y*w*l). */
    record Parsed(int w, int h, int l, BlockState[] palette, int skip, int[] cells) {
    }

    /** A chunk-aligned part of a section: world box [x1..x2] x [y1..y2] x [z1..z2]. */
    record Piece(int x1, int y1, int z1, int x2, int y2, int z2) {
        long volume() {
            return (long) (x2 - x1 + 1) * (y2 - y1 + 1) * (z2 - z1 + 1);
        }
    }

    private final BiConsumer<String, JsonObject> reporter;
    private final Function<String, CompletableFuture<byte[]>> fetch;
    private Job job;

    private static final class Job {
        String id;
        String kind;
        ServerWorld world;
        int[] anchor;
        List<Section> sections = new ArrayList<>();
        int blocksPerTick;
        int sec;
        CompletableFuture<Parsed> pending;
        CompletableFuture<Parsed> next;
        Parsed cur;
        List<Piece> pieces;
        int piece;
        boolean snapshotted;
        long cursor;
        long done;
        long total;
        long changed;
        long lastReport;
        List<Path> restore;
        int restored;
    }

    public ExamplePlacer(BiConsumer<String, JsonObject> reporter, Function<String, CompletableFuture<byte[]>> fetch) {
        this.reporter = reporter;
        this.fetch = fetch;
    }

    /** The install being worked on (sent with the heartbeat), or null. */
    public String current() {
        return job == null ? null : job.id;
    }

    // ------------------------------------------------------------------ pure helpers (unit-tested)
    static int[] decodeVarints(byte[] data, int count, int paletteSize) {
        int[] out = new int[count];
        int n = 0;
        int v = 0;
        int shift = 0;
        for (byte b : data) {
            v |= (b & 0x7F) << shift;
            if ((b & 0x80) != 0) {
                shift += 7;
                if (shift > 21) {
                    throw new IllegalArgumentException("invalid block data");
                }
                continue;
            }
            if (v >= paletteSize || n >= count) {
                throw new IllegalArgumentException("block data doesn't match the palette or size");
            }
            out[n++] = v;
            v = 0;
            shift = 0;
        }
        if (n != count || shift != 0) {
            throw new IllegalArgumentException("block data has the wrong length");
        }
        return out;
    }

    /** Chunk-aligned pieces of the box with min corner o and size s, north-west first. */
    static List<Piece> pieces(int[] o, int[] s) {
        List<Piece> out = new ArrayList<>();
        int x2 = o[0] + s[0] - 1;
        int z2 = o[2] + s[2] - 1;
        for (int cz = o[2] >> 4; cz <= z2 >> 4; cz++) {
            for (int cx = o[0] >> 4; cx <= x2 >> 4; cx++) {
                out.add(new Piece(Math.max(o[0], cx << 4), o[1], Math.max(o[2], cz << 4),
                        Math.min(x2, (cx << 4) + 15), o[1] + s[1] - 1, Math.min(z2, (cz << 4) + 15)));
            }
        }
        return out;
    }

    private static String num(int v) {
        return v < 0 ? "m" + (-v) : Integer.toString(v);
    }

    private static int unnum(String s) {
        return s.startsWith("m") ? -Integer.parseInt(s.substring(1)) : Integer.parseInt(s);
    }

    static String snapshotName(int sec, int x, int y, int z) {
        return "s" + sec + "_" + num(x) + "_" + num(y) + "_" + num(z);
    }

    /** {x, y, z} of a snapshot file name, or null. */
    static int[] snapshotOrigin(String name) {
        Matcher m = SNAPSHOT.matcher(name);
        if (!m.matches()) {
            return null;
        }
        return new int[] {unnum(m.group(2)), unnum(m.group(3)), unnum(m.group(4))};
    }

    static boolean denied(Identifier id) {
        return !"minecraft".equals(id.getNamespace()) || DENIED.contains(id.getPath());
    }

    // ------------------------------------------------------------------ parsing (background thread)
    static Parsed parse(byte[] data, Section s) throws IOException {
        if (data.length != s.bytes() || data.length > MAX_FILE_BYTES || !sha256(data).equals(s.sha256())) {
            throw new IOException("size or SHA-256 doesn't match");
        }
        int w = s.size()[0];
        int h = s.size()[1];
        int l = s.size()[2];
        long cells = (long) w * h * l;
        NbtCompound root = NbtIo.readCompressed(new ByteArrayInputStream(data),
                new NbtSizeTracker(cells * 6 + (8L << 20), 16));
        NbtCompound sch = root.getCompound("Schematic");
        if (sch.getInt("Version") != 3) {
            throw new IOException("not a Sponge v3 schematic");
        }
        if ((sch.getShort("Width") & 0xFFFF) != w || (sch.getShort("Height") & 0xFFFF) != h
                || (sch.getShort("Length") & 0xFFFF) != l) {
            throw new IOException("dimensions differ from the manifest");
        }
        if (sch.contains("Entities") && !sch.getList("Entities", NbtElement.COMPOUND_TYPE).isEmpty()) {
            throw new IOException("entities are not allowed");
        }
        NbtCompound blocks = sch.getCompound("Blocks");
        if (blocks.contains("BlockEntities") && !blocks.getList("BlockEntities", NbtElement.COMPOUND_TYPE).isEmpty()) {
            throw new IOException("block entities are not allowed");
        }
        NbtCompound pal = blocks.getCompound("Palette");
        int n = pal.getSize();
        if (n < 1 || n > 4096) {
            throw new IOException("bad palette size");
        }
        BlockState[] palette = new BlockState[n];
        int skip = -1;
        for (String key : pal.getKeys()) {
            int idx = pal.getInt(key);
            if (idx < 0 || idx >= n || palette[idx] != null) {
                throw new IOException("bad palette index");
            }
            if (SKIP.equals(key)) {
                skip = idx;
                palette[idx] = net.minecraft.block.Blocks.STRUCTURE_VOID.getDefaultState();
                continue;
            }
            BlockState st;
            try {
                st = BlockArgumentParser.block(Registries.BLOCK.getReadOnlyWrapper(), key, false).blockState();
            } catch (Exception e) {
                throw new IOException("invalid block " + key);
            }
            if (denied(Registries.BLOCK.getId(st.getBlock()))) {
                throw new IOException("block not allowed: " + key);
            }
            palette[idx] = st;
        }
        int[] decoded;
        try {
            decoded = decodeVarints(blocks.getByteArray("Data"), (int) cells, n);
        } catch (IllegalArgumentException e) {
            throw new IOException(e.getMessage());
        }
        return new Parsed(w, h, l, palette, skip, decoded);
    }

    static String sha256(byte[] data) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(data));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    // ------------------------------------------------------------------ events (server thread)
    public void place(MinecraftServer server, JsonObject event) {
        String id = event.get("install_id").getAsString();
        if (!INSTALL_ID.matcher(id).matches()) {
            return;
        }
        if (job != null && !job.id.equals(id)) {
            fail(id, "the server is busy with install " + job.id);
            return;
        }
        Job j = new Job();
        j.id = id;
        j.kind = "place";
        j.world = BuildExecutor.world(server, event.get("dimension").getAsString());
        j.anchor = BuildExecutor.ints(event.get("anchor"));
        j.blocksPerTick = Math.max(100, Math.min(50_000, event.get("blocks_per_tick").getAsInt()));
        for (JsonElement el : event.getAsJsonArray("sections")) {
            JsonObject o = el.getAsJsonObject();
            String file = o.get("file").getAsString();
            if (!FILE.matcher(file).matches()) {
                fail(id, "invalid section name");
                return;
            }
            Section s = new Section(file, o.get("sha256").getAsString(), o.get("bytes").getAsInt(),
                    BuildExecutor.ints(o.get("offset")), BuildExecutor.ints(o.get("size")));
            j.sections.add(s);
        }
        if (j.world == null) {
            fail(id, "unknown dimension");
            return;
        }
        for (Section s : j.sections) {
            int y1 = j.anchor[1] + s.offset()[1];
            if (y1 < j.world.getBottomY() || y1 + s.size()[1] > j.world.getTopY()) {
                fail(id, "the pack doesn't fit in this dimension's height");
                return;
            }
        }
        job = j;
        McaiMod.LOG.info("[mcai] example install {}: {} sections", id, j.sections.size());
    }

    public void rollback(MinecraftServer server, JsonObject event) {
        String id = event.get("install_id").getAsString();
        if (!INSTALL_ID.matcher(id).matches()) {
            return;
        }
        if (job != null) {
            report(id, "rollback_failed", "the server is busy with install " + job.id);
            return;
        }
        Job j = new Job();
        j.id = id;
        j.kind = "rollback";
        j.world = BuildExecutor.world(server, event.get("dimension").getAsString());
        Path dir = snapshotDir(server, id);
        try (Stream<Path> files = Files.list(dir)) {
            j.restore = files.filter(p -> p.getFileName().toString().endsWith(".nbt")).sorted().toList();
        } catch (IOException e) {
            j.restore = List.of();
        }
        if (j.world == null || j.restore.isEmpty()) {
            report(id, "rollback_failed", "no undo snapshots found");
            return;
        }
        job = j;
        McaiMod.LOG.info("[mcai] example rollback {}: {} pieces", id, j.restore.size());
    }

    public void cancel(JsonObject event) {
        String id = event.get("install_id").getAsString();
        if (job != null && job.id.equals(id) && "place".equals(job.kind)) {
            JsonObject r = progress(job);
            r.addProperty("status", "cancelled");
            reporter.accept(id, r);
            job = null;
        }
    }

    public void forget(MinecraftServer server, JsonObject event) {
        String id = event.get("install_id").getAsString();
        if (!INSTALL_ID.matcher(id).matches() || (job != null && job.id.equals(id))) {
            return;
        }
        StructureTemplateManager mgr = server.getStructureTemplateManager();
        Path dir = snapshotDir(server, id);
        try (Stream<Path> files = Files.list(dir)) {
            for (Path p : files.toList()) {
                String name = p.getFileName().toString().replace(".nbt", "");
                if (snapshotOrigin(name) != null) {
                    mgr.unloadTemplate(snapshotId(id, name));
                }
                Files.deleteIfExists(p);
            }
            Files.deleteIfExists(dir);
        } catch (IOException e) {
            McaiMod.LOG.warn("[mcai] could not delete snapshots of {}: {}", id, e.toString());
        }
        report(id, "forgotten", null);
    }

    /**
     * True when the chunk is loaded. Otherwise it is requested with a ticket and loads (or generates) in the
     * background: the server thread never waits for a chunk, which on slow hosts could exceed the watchdog.
     */
    static boolean chunkReady(ServerWorld world, int cx, int cz) {
        ChunkPos p = new ChunkPos(cx, cz);
        world.getChunkManager().addTicket(TICKET, p, 0, p);
        // Not isChunkLoaded(): in 1.21.1 that is true as soon as the ticket raises the chunk's level, while the chunk
        // may still be loading or generating, and the next block read then waits for it on the server thread (seen
        // as a watchdog kill while placing Atlantis Full in ungenerated terrain). getWorldChunk() never waits: it
        // returns the chunk only once it is fully loaded.
        return world.getChunkManager().getWorldChunk(cx, cz) != null;
    }

    static void releaseChunk(ServerWorld world, int cx, int cz) {
        ChunkPos p = new ChunkPos(cx, cz);
        world.getChunkManager().removeTicket(TICKET, p, 0, p);
    }

    static Identifier snapshotId(String install, String name) {
        return Identifier.of("mcai", "examples/" + install + "/" + name);
    }

    static Path snapshotDir(MinecraftServer server, String install) {
        return server.getSavePath(WorldSavePath.GENERATED).resolve("mcai").resolve("structures").resolve("examples")
                .resolve(install);
    }

    // ------------------------------------------------------------------ work (every tick)
    public void tick(MinecraftServer server) {
        Job j = job;
        if (j == null) {
            return;
        }
        try {
            if ("rollback".equals(j.kind)) {
                rollbackStep(server, j);
            } else {
                placeStep(server, j);
            }
            if (job == j && server.getTicks() - j.lastReport >= 40) {
                j.lastReport = server.getTicks();
                JsonObject r = progress(j);
                r.addProperty("status", "progress");
                reporter.accept(j.id, r);
            }
        } catch (Exception e) {
            McaiMod.LOG.error("[mcai] example job {} failed", j.id, e);
            job = null;
            if ("rollback".equals(j.kind)) {
                report(j.id, "rollback_failed", "error while restoring");
            } else {
                fail(j.id, e.getMessage() == null ? "error while placing" : e.getMessage());
            }
        }
    }

    private CompletableFuture<Parsed> load(Job j, int idx) {
        Section s = j.sections.get(idx);
        return fetch.apply("/v1/mod/examples/" + j.id + "/" + s.file()).thenApplyAsync(bytes -> {
            try {
                return parse(bytes, s);
            } catch (IOException e) {
                throw new IllegalStateException(s.file() + ": " + e.getMessage());
            }
        });
    }

    private void placeStep(MinecraftServer server, Job j) throws Exception {
        if (j.cur == null) {
            if (j.pending == null) {
                j.pending = j.next != null ? j.next : load(j, j.sec);
                j.next = null;
            }
            if (!j.pending.isDone()) {
                return;
            }
            try {
                j.cur = j.pending.join();
            } catch (Exception e) {
                Throwable c = e.getCause() != null ? e.getCause() : e;
                throw new IllegalStateException(c.getMessage() == null ? "download failed" : c.getMessage());
            }
            j.pending = null;
            Section s = j.sections.get(j.sec);
            int[] o = {j.anchor[0] + s.offset()[0], j.anchor[1] + s.offset()[1], j.anchor[2] + s.offset()[2]};
            j.pieces = pieces(o, s.size());
            j.piece = 0;
            j.cursor = 0;
            j.snapshotted = false;
            if (j.sec + 1 < j.sections.size()) {
                j.next = load(j, j.sec + 1);           // download and parse the next section meanwhile
            }
        }
        Section s = j.sections.get(j.sec);
        Parsed p = j.cur;
        int ox = j.anchor[0] + s.offset()[0];
        int oy = j.anchor[1] + s.offset()[1];
        int oz = j.anchor[2] + s.offset()[2];
        long budget = j.blocksPerTick;
        long checks = (long) j.blocksPerTick * 8;
        int snapshots = 0;
        StructureTemplateManager mgr = server.getStructureTemplateManager();
        BlockPos.Mutable pos = new BlockPos.Mutable();
        while (budget > 0 && checks > 0) {
            if (j.piece >= j.pieces.size()) {
                j.sec++;
                j.cur = null;
                if (j.sec >= j.sections.size()) {
                    job = null;
                    JsonObject r = progress(j);
                    r.addProperty("status", "placed");
                    reporter.accept(j.id, r);
                    McaiMod.LOG.info("[mcai] example install {} done: {} blocks changed", j.id, j.changed);
                }
                return;
            }
            Piece pc = j.pieces.get(j.piece);
            if (j.snapshotted && !chunkReady(j.world, pc.x1() >> 4, pc.z1() >> 4)) {
                return;                                 // unloaded between ticks: wait for it
            }
            if (!j.snapshotted) {
                if (snapshots >= 2) {
                    return;                             // at most two snapshots per tick
                }
                if (!chunkReady(j.world, pc.x1() >> 4, pc.z1() >> 4)) {
                    return;                             // loading in the background; try again next tick
                }
                String name = snapshotName(j.sec, pc.x1(), pc.y1(), pc.z1());
                if (!Files.exists(snapshotDir(server, j.id).resolve(name + ".nbt"))) {
                    Identifier sid = snapshotId(j.id, name);
                    StructureTemplate t = mgr.getTemplateOrBlank(sid);
                    t.saveFromWorld(j.world, new BlockPos(pc.x1(), pc.y1(), pc.z1()),
                            new Vec3i(pc.x2() - pc.x1() + 1, pc.y2() - pc.y1() + 1, pc.z2() - pc.z1() + 1), false, null);
                    if (!mgr.saveTemplate(sid)) {
                        throw new IllegalStateException("could not save an undo snapshot");
                    }
                    mgr.unloadTemplate(sid);
                }
                j.snapshotted = true;
                snapshots++;
            }
            int pw = pc.x2() - pc.x1() + 1;
            int pl = pc.z2() - pc.z1() + 1;
            long vol = pc.volume();
            while (j.cursor < vol && budget > 0 && checks > 0) {
                long c = j.cursor++;
                int x = pc.x1() + (int) (c % pw);
                int z = pc.z1() + (int) ((c / pw) % pl);
                int y = pc.y1() + (int) (c / ((long) pw * pl));
                int idx = p.cells()[(x - ox) + (z - oz) * p.w() + (y - oy) * p.w() * p.l()];
                if (idx == p.skip()) {
                    continue;
                }
                checks--;
                j.done++;
                BlockState target = p.palette()[idx];
                pos.set(x, y, z);
                if (j.world.getBlockState(pos) != target) {
                    Clearable.clear(j.world.getBlockEntity(pos));     // like /fill: no item drops
                    j.world.setBlockState(pos, target, FLAGS);
                    j.changed++;
                    budget--;
                }
            }
            if (j.cursor >= vol) {
                releaseChunk(j.world, pc.x1() >> 4, pc.z1() >> 4);
                j.piece++;
                j.cursor = 0;
                j.snapshotted = false;
            }
        }
    }

    private void rollbackStep(MinecraftServer server, Job j) throws IOException {
        StructureTemplateManager mgr = server.getStructureTemplateManager();
        if (j.restored < j.restore.size()) {
            Path f = j.restore.get(j.restored);
            String name = f.getFileName().toString().replace(".nbt", "");
            int[] o = snapshotOrigin(name);
            if (o != null && !chunkReady(j.world, o[0] >> 4, o[2] >> 4)) {
                return;                                 // loading in the background; try again next tick
            }
            j.restored++;
            if (o != null) {
                Identifier sid = snapshotId(j.id, name);
                StructureTemplate t = mgr.getTemplate(sid).orElse(null);
                if (t != null) {
                    BlockPos at = new BlockPos(o[0], o[1], o[2]);
                    // FORCE_STATE: restore verbatim, no neighbour shape updates (they could load chunks outside the
                    // area synchronously and stall the server thread)
                    t.place(j.world, at, at, new StructurePlacementData().setUpdateNeighbors(false), j.world.getRandom(),
                            FLAGS);
                }
                mgr.unloadTemplate(sid);
                releaseChunk(j.world, o[0] >> 4, o[2] >> 4);
            }
            j.done = j.restored;
            j.total = j.restore.size();
            return;
        }
        for (Path f : j.restore) {
            Files.deleteIfExists(f);
        }
        Files.deleteIfExists(snapshotDir(server, j.id));
        job = null;
        JsonObject r = new JsonObject();
        r.addProperty("status", "rolled_back");
        r.addProperty("pieces", j.restore.size());
        reporter.accept(j.id, r);
        McaiMod.LOG.info("[mcai] example install {} rolled back ({} pieces)", j.id, j.restore.size());
    }

    /** Progress for the gateway: cells checked and changed (placing), or pieces restored of all (rollback). */
    private static JsonObject progress(Job j) {
        JsonObject r = new JsonObject();
        r.addProperty("done", j.done);
        r.addProperty("changed", j.changed);
        if ("rollback".equals(j.kind)) {
            r.addProperty("total", j.total);
        }
        return r;
    }

    private void fail(String id, String error) {
        JsonObject r = job != null && job.id.equals(id) ? progress(job) : new JsonObject();
        r.addProperty("status", "failed");
        r.addProperty("error", error);
        reporter.accept(id, r);
    }

    private void report(String id, String status, String error) {
        JsonObject r = new JsonObject();
        r.addProperty("status", status);
        if (error != null) {
            r.addProperty("error", error);
        }
        reporter.accept(id, r);
    }
}
