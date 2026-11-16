package dev.mcai.mod;

import java.util.List;

/**
 * Walks the block positions of a validated build plan in order, one position per call, so a build can be
 * spread over many server ticks. Pure Java (no Minecraft classes) so it can be unit-tested.
 * Fill modes match the gateway's validator: replace (every block), hollow (shell = block, inside = air),
 * outline (shell only, inside untouched).
 */
public final class PlanCursor {
    /** One action of the plan: a box (a setblock is a 1x1x1 box) with its block key and mode. */
    public record Action(int[] from, int[] to, String block, String mode) {
    }

    /** The next position to place: coordinates plus the block key ("air" marks the inside of a hollow fill). */
    public record Step(int x, int y, int z, String block) {
    }

    public static final String AIR = "minecraft:air";
    private final List<Action> actions;
    private int index;
    private int x, y, z;
    private boolean started;
    private long emitted;

    public PlanCursor(List<Action> actions) {
        this.actions = List.copyOf(actions);
    }

    public long emitted() {
        return emitted;
    }

    /** The next step, or null when the plan is finished. */
    public Step next() {
        while (index < actions.size()) {
            Action a = actions.get(index);
            if (!started) {
                x = a.from()[0];
                y = a.from()[1];
                z = a.from()[2];
                started = true;
            } else if (!advance(a)) {
                index++;
                started = false;
                continue;
            }
            boolean shell = x == a.from()[0] || x == a.to()[0] || y == a.from()[1] || y == a.to()[1]
                    || z == a.from()[2] || z == a.to()[2];
            String block;
            if (shell || "replace".equals(a.mode())) {
                block = a.block();
            } else if ("hollow".equals(a.mode())) {
                block = AIR;
            } else {
                continue;   // outline: leave the inside alone
            }
            emitted++;
            return new Step(x, y, z, block);
        }
        return null;
    }

    /** Move to the next position inside the action's box (x fastest, then z, then y: bottom layer first). */
    private boolean advance(Action a) {
        if (x < a.to()[0]) {
            x++;
            return true;
        }
        x = a.from()[0];
        if (z < a.to()[2]) {
            z++;
            return true;
        }
        z = a.from()[2];
        if (y < a.to()[1]) {
            y++;
            return true;
        }
        return false;
    }
}
