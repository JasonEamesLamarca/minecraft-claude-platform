package dev.mcai.mod;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class ChatTextTest {
    @Test
    void stripsFormattingCodes() {
        assertEquals("red bold", ChatText.clean("§cred §lbold"));
    }

    @Test
    void stripsControlAndBidiCharacters() {
        assertEquals("a b c", ChatText.clean("a\nb\u0007‮ c"));
    }

    @Test
    void capsLength() {
        assertTrue(ChatText.clean("x".repeat(10_000)).length() <= ChatText.MAX_LENGTH);
    }

    @Test
    void nullIsEmpty() {
        assertEquals("", ChatText.clean(null));
    }

    @Test
    void configReadsTokenFromFile(@TempDir Path dir) throws Exception {
        Path f = dir.resolve("token");
        Files.writeString(f, "t".repeat(40) + "\n");
        McaiConfig c = McaiConfig.fromEnvironment(Map.of("MCAI_GATEWAY_TOKEN_FILE", f.toString()));
        assertEquals("t".repeat(40), c.token());
        assertEquals("ai", c.commandName());
        assertEquals(0, c.askPermissionLevel());
        assertTrue(c.allowConsole());
    }

    @Test
    void configRejectsShortToken(@TempDir Path dir) throws Exception {
        Path f = dir.resolve("token");
        Files.writeString(f, "short");
        assertThrows(IllegalArgumentException.class,
                () -> McaiConfig.fromEnvironment(Map.of("MCAI_GATEWAY_TOKEN_FILE", f.toString())));
    }

    @Test
    void configRejectsBadCommandNameAndScheme(@TempDir Path dir) throws Exception {
        Path f = dir.resolve("token");
        Files.writeString(f, "t".repeat(40));
        assertThrows(IllegalArgumentException.class, () -> McaiConfig.fromEnvironment(
                Map.of("MCAI_GATEWAY_TOKEN_FILE", f.toString(), "MCAI_COMMAND", "op me")));
        assertThrows(IllegalArgumentException.class, () -> McaiConfig.fromEnvironment(
                Map.of("MCAI_GATEWAY_TOKEN_FILE", f.toString(), "MCAI_GATEWAY_URL", "file:///etc/passwd")));
        assertFalse(McaiConfig.COMMAND_NAME.matcher("Ai").matches());
    }
}
