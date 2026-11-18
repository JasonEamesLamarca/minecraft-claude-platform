package dev.mcai.mod;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.function.Consumer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * HTTP client for the gateway's mod API. Requests go out asynchronously; replies come back through a
 * long-poll loop on a daemon thread. Nothing received from the gateway is ever executed as a command: the mod
 * shows text, places validated block data (builds, example packs) and reports back.
 */
public final class GatewayClient {
    private static final Logger LOG = LoggerFactory.getLogger("mcai");
    private final McaiConfig config;
    private final HttpClient http;
    private volatile boolean running;
    private Thread poller;

    public GatewayClient(McaiConfig config) {
        this.config = config;
        this.http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
    }

    private HttpRequest.Builder request(String path) {
        return HttpRequest.newBuilder(config.gatewayUrl().resolve(path))
                .header("Authorization", "Bearer " + config.token())
                .header("Content-Type", "application/json")
                .header("User-Agent", "mcai-fabric");
    }

    /** POST a JSON body; the future completes with the parsed JSON reply (or an error object). */
    public CompletableFuture<JsonObject> post(String path, JsonObject body) {
        HttpRequest req = request(path).timeout(Duration.ofSeconds(10))
                .POST(HttpRequest.BodyPublishers.ofString(body.toString(), StandardCharsets.UTF_8)).build();
        return send(req);
    }

    public CompletableFuture<JsonObject> get(String path) {
        return send(request(path).timeout(Duration.ofSeconds(10)).GET().build());
    }

    /** GET a binary file (example pack sections). Fails on any status but 200 or a body above maxBytes. */
    public CompletableFuture<byte[]> getBytes(String path, int maxBytes) {
        HttpRequest req = request(path).timeout(Duration.ofSeconds(60)).GET().build();
        return http.sendAsync(req, HttpResponse.BodyHandlers.ofByteArray()).thenApply(resp -> {
            if (resp.statusCode() != 200) {
                throw new IllegalStateException("download failed (HTTP " + resp.statusCode() + ")");
            }
            if (resp.body().length > maxBytes) {
                throw new IllegalStateException("file too large");
            }
            return resp.body();
        });
    }

    private CompletableFuture<JsonObject> send(HttpRequest req) {
        return http.sendAsync(req, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8))
                .thenApply(resp -> {
                    JsonObject json = parse(resp.body());
                    if (resp.statusCode() >= 400 && !json.has("message")) {
                        json.addProperty("message", "Gateway error (HTTP " + resp.statusCode() + ")");
                    }
                    json.addProperty("_status", resp.statusCode());
                    return json;
                })
                .exceptionally(e -> {
                    LOG.warn("[mcai] gateway unreachable: {}", e.toString());
                    JsonObject err = new JsonObject();
                    err.addProperty("accepted", false);
                    err.addProperty("message", "The AI service is not reachable right now. Try again later.");
                    err.addProperty("_status", 0);
                    return err;
                });
    }

    private static JsonObject parse(String body) {
        try {
            JsonElement el = JsonParser.parseString(body);
            return el.isJsonObject() ? el.getAsJsonObject() : new JsonObject();
        } catch (RuntimeException e) {
            return new JsonObject();
        }
    }

    /** Start the long-poll loop that delivers gateway messages to the given consumer (one per message). */
    public synchronized void start(Consumer<JsonObject> onMessage) {
        if (running) {
            return;
        }
        running = true;
        poller = new Thread(() -> pollLoop(onMessage), "mcai-gateway-poller");
        poller.setDaemon(true);
        poller.start();
    }

    public synchronized void stop() {
        running = false;
        if (poller != null) {
            poller.interrupt();
        }
    }

    private void pollLoop(Consumer<JsonObject> onMessage) {
        long backoffMs = 1000;
        while (running) {
            try {
                HttpRequest req = request("/v1/mod/events?wait=25").timeout(Duration.ofSeconds(40)).GET().build();
                HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
                if (resp.statusCode() != 200) {
                    throw new IllegalStateException("HTTP " + resp.statusCode());
                }
                JsonElement events = parse(resp.body()).get("events");
                if (events instanceof JsonArray arr) {
                    for (JsonElement e : arr) {
                        if (e.isJsonObject()) {
                            onMessage.accept(e.getAsJsonObject());
                        }
                    }
                }
                backoffMs = 1000;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            } catch (Exception e) {
                if (!running) {
                    return;
                }
                LOG.debug("[mcai] event poll failed: {}", e.toString());
                try {
                    Thread.sleep(backoffMs);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    return;
                }
                backoffMs = Math.min(backoffMs * 2, 30_000);
            }
        }
    }
}
