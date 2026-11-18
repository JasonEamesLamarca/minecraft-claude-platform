package dev.mcai.mod;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import net.minecraft.util.Identifier;
import org.junit.jupiter.api.Test;

class ExamplePlacerTest {
    @Test
    void varints() {
        assertArrayEquals(new int[] {0, 1, 127}, ExamplePlacer.decodeVarints(new byte[] {0, 1, 127}, 3, 128));
        assertArrayEquals(new int[] {0, 128, 300},
                ExamplePlacer.decodeVarints(new byte[] {0, (byte) 0x80, 0x01, (byte) 0xAC, 0x02}, 3, 301));
    }

    @Test
    void hostileBlockDataIsRejected() {
        assertThrows(IllegalArgumentException.class, () -> ExamplePlacer.decodeVarints(new byte[] {5}, 1, 2),
                "index outside the palette");
        assertThrows(IllegalArgumentException.class, () -> ExamplePlacer.decodeVarints(new byte[] {0, 0}, 3, 2),
                "too short");
        assertThrows(IllegalArgumentException.class, () -> ExamplePlacer.decodeVarints(new byte[] {0, 0, 0, 0}, 3, 2),
                "too long");
        byte[] runaway = {(byte) 0x80, (byte) 0x80, (byte) 0x80, (byte) 0x80, 0x01};
        assertThrows(IllegalArgumentException.class, () -> ExamplePlacer.decodeVarints(runaway, 1, 2), "overlong");
        assertThrows(IllegalArgumentException.class,
                () -> ExamplePlacer.decodeVarints(new byte[] {(byte) 0x81}, 1, 200), "unterminated");
    }

    @Test
    void piecesAreChunkAligned() {
        List<ExamplePlacer.Piece> p = ExamplePlacer.pieces(new int[] {-48, -64, -640}, new int[] {96, 127, 128});
        assertEquals(6 * 8, p.size());
        assertEquals(new ExamplePlacer.Piece(-48, -64, -640, -33, 62, -625), p.get(0));
        long cells = 0;
        for (ExamplePlacer.Piece x : p) {
            assertEquals(x.x1() >> 4, x.x2() >> 4, "one chunk per piece");
            assertEquals(x.z1() >> 4, x.z2() >> 4);
            cells += x.volume();
        }
        assertEquals(96L * 127 * 128, cells, "pieces cover the section exactly");
        List<ExamplePlacer.Piece> odd = ExamplePlacer.pieces(new int[] {5, 0, 5}, new int[] {20, 3, 4});
        assertEquals(2, odd.size());
        assertEquals(new ExamplePlacer.Piece(5, 0, 5, 15, 2, 8), odd.get(0));
        assertEquals(new ExamplePlacer.Piece(16, 0, 5, 24, 2, 8), odd.get(1));
    }

    @Test
    void snapshotNamesRoundTrip() {
        String n = ExamplePlacer.snapshotName(3, -48, -64, 1200);
        assertEquals("s3_m48_m64_1200", n);
        assertArrayEquals(new int[] {-48, -64, 1200}, ExamplePlacer.snapshotOrigin(n));
        assertNull(ExamplePlacer.snapshotOrigin("../../level"));
        assertNull(ExamplePlacer.snapshotOrigin("s1_2_3"));
        assertTrue(Identifier.isPathValid("examples/0123abcd/" + n));
    }

    @Test
    void denyList() {
        assertTrue(ExamplePlacer.denied(Identifier.of("minecraft", "command_block")));
        assertTrue(ExamplePlacer.denied(Identifier.of("minecraft", "bedrock")));
        assertTrue(ExamplePlacer.denied(Identifier.of("othermod", "stone")), "only vanilla blocks");
        assertFalse(ExamplePlacer.denied(Identifier.of("minecraft", "light")));
        assertFalse(ExamplePlacer.denied(Identifier.of("minecraft", "sea_lantern")));
    }

    @Test
    void namesAreStrict() {
        assertTrue(ExamplePlacer.FILE.matcher("atlantis-lite-s000.schem").matches());
        assertFalse(ExamplePlacer.FILE.matcher("../x.schem").matches());
        assertFalse(ExamplePlacer.INSTALL_ID.matcher("../../x").matches());
    }
}
