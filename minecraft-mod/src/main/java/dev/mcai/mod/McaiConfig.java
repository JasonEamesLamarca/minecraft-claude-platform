package dev.mcai.mod;

import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * Mod settings, read from environment variables (set in compose.yaml). The gateway token is read from a
 * file (a Docker secret), never from an environment variable or a config file inside the world folder.
 */
public record McaiConfig(URI gatewayUrl, String token, String commandName, int askPermissionLevel,
                         boolean allowConsole) {

    static final Pattern COMMAND_NAME = Pattern.compile("[a-z][a-z0-9_]{0,15}");

    public static McaiConfig fromEnvironment(Map<String, String> env) throws IOException {
        URI url = URI.create(env.getOrDefault("MCAI_GATEWAY_URL", "http://gateway:8080"));
        if (!"http".equals(url.getScheme()) && !"https".equals(url.getScheme())) {
            throw new IllegalArgumentException("MCAI_GATEWAY_URL must be http(s): " + url);
        }
        Path tokenFile = Path.of(env.getOrDefault("MCAI_GATEWAY_TOKEN_FILE", "/run/secrets/gateway_mod_token"));
        String token = Files.readString(tokenFile, StandardCharsets.UTF_8).trim();
        if (token.length() < 32) {
            throw new IllegalArgumentException("Gateway token in " + tokenFile + " is missing or too short");
        }
        String command = env.getOrDefault("MCAI_COMMAND", "ai").trim().toLowerCase();
        if (!COMMAND_NAME.matcher(command).matches()) {
            throw new IllegalArgumentException("MCAI_COMMAND must match " + COMMAND_NAME.pattern());
        }
        int level = Integer.parseInt(env.getOrDefault("MCAI_ASK_PERMISSION_LEVEL", "0").trim());
        if (level < 0 || level > 4) {
            throw new IllegalArgumentException("MCAI_ASK_PERMISSION_LEVEL must be 0-4");
        }
        boolean console = Boolean.parseBoolean(env.getOrDefault("MCAI_ALLOW_CONSOLE", "true").trim());
        return new McaiConfig(url, token, command, level, console);
    }
}
