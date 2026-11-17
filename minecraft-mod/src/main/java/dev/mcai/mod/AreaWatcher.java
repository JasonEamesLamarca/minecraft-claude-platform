package dev.mcai.mod;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;

/**
 * Watches the areas the gateway sends with each heartbeat reply (one per enabled area automation) and reports
 * players entering or leaving them. Pure logic, no Minecraft types, so it is unit-tested.
 *
 * <p>A new or changed area starts with a baseline: players already inside are not reported. Afterwards a player
 * who appears inside (walking in, teleporting or logging in there) "enters"; a player who is still online but no
 * longer inside "leaves" (logging out doesn't count as leaving).
 */
final class AreaWatcher {
    static final int MAX_AREAS = 200;
    private static final Pattern ID = Pattern.compile("[a-z][a-z0-9-]{1,39}");
    private static final Pattern DIM = Pattern.compile("[a-z0-9_.-]{1,32}:[a-z0-9_./-]{1,64}");

    record Area(String id, boolean onEnter, String dimension, int minX, int minY, int minZ, int maxX, int maxY,
                int maxZ) {
        boolean contains(Pos p) {
            return dimension.equals(p.dimension()) && p.x() >= minX && p.x() <= maxX && p.y() >= minY
                    && p.y() <= maxY && p.z() >= minZ && p.z() <= maxZ;
        }
    }

    record Pos(UUID id, String name, String dimension, int x, int y, int z) {}

    record Crossing(String areaId, boolean enter, UUID player, String name) {}

    private Map<String, Area> areas = Map.of();
    private final Map<String, Set<UUID>> inside = new HashMap<>();
    private final Set<String> fresh = new HashSet<>();

    synchronized void setAreas(List<Area> list) {
        Map<String, Area> next = new LinkedHashMap<>();
        for (Area a : list) {
            if (next.size() < MAX_AREAS) {
                next.put(a.id(), a);
            }
        }
        for (Map.Entry<String, Area> e : next.entrySet()) {
            if (!e.getValue().equals(areas.get(e.getKey()))) {
                fresh.add(e.getKey());
                inside.remove(e.getKey());
            }
        }
        inside.keySet().retainAll(next.keySet());
        fresh.retainAll(next.keySet());
        areas = next;
    }

    synchronized int size() {
        return areas.size();
    }

    synchronized List<Crossing> check(List<Pos> players) {
        List<Crossing> out = new ArrayList<>();
        Set<UUID> online = new HashSet<>();
        for (Pos p : players) {
            online.add(p.id());
        }
        for (Area a : areas.values()) {
            Set<UUID> now = new HashSet<>();
            Map<UUID, String> names = new HashMap<>();
            for (Pos p : players) {
                names.put(p.id(), p.name());
                if (a.contains(p)) {
                    now.add(p.id());
                }
            }
            Set<UUID> before = inside.getOrDefault(a.id(), Set.of());
            if (!fresh.remove(a.id())) {
                if (a.onEnter()) {
                    for (UUID u : now) {
                        if (!before.contains(u)) {
                            out.add(new Crossing(a.id(), true, u, names.get(u)));
                        }
                    }
                } else {
                    for (UUID u : before) {
                        if (!now.contains(u) && online.contains(u)) {
                            out.add(new Crossing(a.id(), false, u, names.get(u)));
                        }
                    }
                }
            }
            inside.put(a.id(), now);
        }
        return out;
    }

    /** Areas from the gateway's heartbeat reply; malformed entries are skipped. */
    static List<Area> parse(JsonElement json) {
        List<Area> out = new ArrayList<>();
        if (!(json instanceof JsonArray arr)) {
            return out;
        }
        for (JsonElement el : arr) {
            try {
                JsonObject o = el.getAsJsonObject();
                String id = o.get("id").getAsString();
                String dim = o.get("dimension").getAsString();
                String on = o.get("on").getAsString();
                JsonArray f = o.getAsJsonArray("from");
                JsonArray t = o.getAsJsonArray("to");
                if (!ID.matcher(id).matches() || !DIM.matcher(dim).matches() || f.size() != 3 || t.size() != 3
                        || !("enter".equals(on) || "leave".equals(on))) {
                    continue;
                }
                int[] a = {f.get(0).getAsInt(), f.get(1).getAsInt(), f.get(2).getAsInt()};
                int[] b = {t.get(0).getAsInt(), t.get(1).getAsInt(), t.get(2).getAsInt()};
                out.add(new Area(id, "enter".equals(on), dim, Math.min(a[0], b[0]), Math.min(a[1], b[1]),
                        Math.min(a[2], b[2]), Math.max(a[0], b[0]), Math.max(a[1], b[1]), Math.max(a[2], b[2])));
            } catch (RuntimeException e) {
                // skip malformed area
            }
        }
        return out;
    }
}
