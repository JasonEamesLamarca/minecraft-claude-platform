package dev.mcai.mod;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonParser;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class AreaWatcherTest {
    private static final UUID A = UUID.fromString("11111111-2222-3333-4444-555555555555");
    private static final UUID B = UUID.fromString("66666666-7777-8888-9999-000000000000");
    private static final String OW = "minecraft:overworld";

    private static AreaWatcher.Area box(String id, boolean enter) {
        return new AreaWatcher.Area(id, enter, OW, 0, 60, 0, 10, 80, 10);
    }

    private static AreaWatcher.Pos at(UUID id, int x, int y, int z) {
        return new AreaWatcher.Pos(id, id.equals(A) ? "Alex" : "Bo", OW, x, y, z);
    }

    @Test
    void baselineThenEnterOnce() {
        AreaWatcher w = new AreaWatcher();
        w.setAreas(List.of(box("plaza", true)));
        assertTrue(w.check(List.of(at(A, 5, 64, 5))).isEmpty(), "already inside when the area appears: no event");
        assertTrue(w.check(List.of(at(A, 5, 64, 5), at(B, 50, 64, 50))).isEmpty());
        List<AreaWatcher.Crossing> c = w.check(List.of(at(A, 5, 64, 5), at(B, 9, 70, 1)));
        assertEquals(1, c.size());
        assertEquals(B, c.get(0).player());
        assertTrue(c.get(0).enter());
        assertTrue(w.check(List.of(at(A, 5, 64, 5), at(B, 9, 70, 1))).isEmpty(), "staying inside: no repeat");
    }

    @Test
    void leaveCountsOnlyWhileOnline() {
        AreaWatcher w = new AreaWatcher();
        w.setAreas(List.of(box("gate", false)));
        w.check(List.of(at(A, 1, 64, 1), at(B, 2, 64, 2)));
        List<AreaWatcher.Crossing> c = w.check(List.of(at(A, 20, 64, 1)));   // A walked out, B logged out
        assertEquals(1, c.size());
        assertEquals(A, c.get(0).player());
        assertTrue(!c.get(0).enter());
    }

    @Test
    void otherDimensionAndUnchangedAreasKeepState() {
        AreaWatcher w = new AreaWatcher();
        w.setAreas(List.of(box("plaza", true)));
        w.check(List.of());
        assertTrue(w.check(List.of(new AreaWatcher.Pos(A, "Alex", "minecraft:the_nether", 5, 64, 5))).isEmpty());
        w.setAreas(List.of(box("plaza", true)));                // same area re-sent by the gateway
        assertEquals(1, w.check(List.of(at(A, 5, 64, 5))).size());
    }

    @Test
    void parseSkipsMalformed() {
        String json = "[{\"id\":\"plaza\",\"on\":\"enter\",\"dimension\":\"minecraft:overworld\",\"from\":[10,60,10],"
                + "\"to\":[0,80,0]},{\"id\":\"BAD ID\",\"on\":\"enter\",\"dimension\":\"minecraft:overworld\","
                + "\"from\":[0,0,0],\"to\":[1,1,1]},{\"id\":\"x1\",\"on\":\"sideways\"},\"junk\"]";
        List<AreaWatcher.Area> areas = AreaWatcher.parse(JsonParser.parseString(json));
        assertEquals(1, areas.size());
        assertEquals(0, areas.get(0).minX());
        assertEquals(10, areas.get(0).maxX());
    }
}
