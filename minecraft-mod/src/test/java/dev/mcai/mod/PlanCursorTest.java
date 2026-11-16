package dev.mcai.mod;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

import java.util.HashSet;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;

class PlanCursorTest {
    private static PlanCursor.Action box(int x2, int y2, int z2, String mode) {
        return new PlanCursor.Action(new int[] {0, 0, 0}, new int[] {x2, y2, z2}, "minecraft:stone", mode);
    }

    private static int count(PlanCursor c, String block) {
        int n = 0;
        for (PlanCursor.Step s; (s = c.next()) != null; ) {
            if (block == null || block.equals(s.block())) {
                n++;
            }
        }
        return n;
    }

    @Test
    void replaceVisitsEveryBlockOnce() {
        PlanCursor c = new PlanCursor(List.of(box(2, 2, 2, "replace")));
        Set<String> seen = new HashSet<>();
        for (PlanCursor.Step s; (s = c.next()) != null; ) {
            seen.add(s.x() + "," + s.y() + "," + s.z());
        }
        assertEquals(27, seen.size());
        assertEquals(27, c.emitted());
    }

    @Test
    void hollowPlacesShellAndAirInside() {
        assertEquals(26, count(new PlanCursor(List.of(box(2, 2, 2, "hollow"))), "minecraft:stone"));
        assertEquals(1, count(new PlanCursor(List.of(box(2, 2, 2, "hollow"))), PlanCursor.AIR));
    }

    @Test
    void outlineSkipsInside() {
        PlanCursor c = new PlanCursor(List.of(box(2, 2, 2, "outline")));
        assertEquals(26, count(c, null));
    }

    @Test
    void bottomLayerFirstAndActionsInOrder() {
        PlanCursor c = new PlanCursor(List.of(box(1, 1, 1, "replace"),
                new PlanCursor.Action(new int[] {5, 5, 5}, new int[] {5, 5, 5}, "minecraft:torch", "replace")));
        for (int i = 0; i < 4; i++) {
            assertEquals(0, c.next().y());
        }
        for (int i = 0; i < 4; i++) {
            assertEquals(1, c.next().y());
        }
        assertEquals("minecraft:torch", c.next().block());
        assertNull(c.next());
        assertNull(c.next());
    }

    @Test
    void countsMatchGatewayValidator() {
        // Same formulas as mcai.plan.box_count (Python): replace/hollow = whole box, outline = box - inside.
        assertEquals(10 * 5 * 7, count(new PlanCursor(List.of(box(9, 4, 6, "hollow"))), null));
        assertEquals(10 * 5 * 7 - 8 * 3 * 5, count(new PlanCursor(List.of(box(9, 4, 6, "outline"))), null));
        assertEquals(1, count(new PlanCursor(List.of(box(0, 0, 0, "hollow"))), null));
    }
}
