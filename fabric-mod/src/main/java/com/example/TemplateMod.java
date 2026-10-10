package com.example;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.minecraft.server.MinecraftServer;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.core.Filter;
import org.apache.logging.log4j.core.Layout;
import org.apache.logging.log4j.core.LogEvent;
import org.apache.logging.log4j.core.LoggerContext;
import org.apache.logging.log4j.core.appender.AbstractAppender;
import org.apache.logging.log4j.core.config.Configuration;
import org.apache.logging.log4j.core.config.LoggerConfig;
import org.apache.logging.log4j.core.config.Property;
import org.apache.logging.log4j.core.layout.PatternLayout;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.Serializable;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.Executors;

public class TemplateMod implements ModInitializer {
	public static final String MOD_ID = "template-mod";
	public static final Logger LOGGER = LoggerFactory.getLogger("DiscordWhitelist");

	private static MinecraftServer minecraftServer;
	private static HttpServer httpServer;
	private static final int HTTP_PORT = 25585;

	public static class DiscordConsoleAppender extends AbstractAppender {
		private static final int MAX_LOGS = 2000;
		public static final ConcurrentLinkedQueue<String> LOG_QUEUE = new ConcurrentLinkedQueue<>();

		protected DiscordConsoleAppender(String name, Filter filter, Layout<? extends Serializable> layout, boolean ignoreExceptions, Property[] properties) {
			super(name, filter, layout, ignoreExceptions, properties);
		}

		public static DiscordConsoleAppender create() {
			PatternLayout layout = PatternLayout.newBuilder()
					.withPattern("[%d{HH:mm:ss}] [%t/%level]: %msg%n")
					.build();
			return new DiscordConsoleAppender("DiscordConsoleAppender", null, layout, true, Property.EMPTY_ARRAY);
		}

		@Override
		public void append(LogEvent event) {
			if (event == null) return;
			byte[] bytes = getLayout().toByteArray(event);
			String formatted = new String(bytes, StandardCharsets.UTF_8).trim();
			if (!formatted.isEmpty()) {
				LOG_QUEUE.add(formatted);
				while (LOG_QUEUE.size() > MAX_LOGS) {
					LOG_QUEUE.poll();
				}
			}
		}
	}

	@Override
	public void onInitialize() {
		LOGGER.info("[DiscordWhitelist] Initializing Discord Whitelist & Console Mod...");

		registerAppender();

		ServerLifecycleEvents.SERVER_STARTED.register(server -> {
			minecraftServer = server;
			startHttpServer();
		});

		ServerLifecycleEvents.SERVER_STOPPING.register(server -> {
			stopHttpServer();
			minecraftServer = null;
		});
	}

	private static void registerAppender() {
		try {
			LoggerContext context = (LoggerContext) LogManager.getContext(false);
			Configuration configuration = context.getConfiguration();
			DiscordConsoleAppender appender = DiscordConsoleAppender.create();
			appender.start();
			configuration.addAppender(appender);
			for (LoggerConfig loggerConfig : configuration.getLoggers().values()) {
				loggerConfig.addAppender(appender, null, null);
			}
			configuration.getRootLogger().addAppender(appender, null, null);
			context.updateLoggers();
			LOGGER.info("[DiscordWhitelist] Discord Console Log4j Appender registered successfully.");
		} catch (Exception e) {
			LOGGER.error("[DiscordWhitelist] Failed to attach Log4j appender: {}", e.getMessage());
		}
	}

	private synchronized void startHttpServer() {
		if (httpServer != null) {
			return;
		}

		try {
			httpServer = HttpServer.create(new InetSocketAddress("127.0.0.1", HTTP_PORT), 0);
			httpServer.setExecutor(Executors.newSingleThreadExecutor());

			httpServer.createContext("/api/whitelist/add", new AddWhitelistHandler());
			httpServer.createContext("/api/whitelist/reload", new ReloadWhitelistHandler());
			httpServer.createContext("/api/console/logs", new GetConsoleLogsHandler());
			httpServer.createContext("/api/console/execute", new ExecuteConsoleCommandHandler());
			httpServer.createContext("/api/status", new StatusHandler());

			httpServer.start();
			LOGGER.info("[DiscordWhitelist] Internal HTTP API listening on 127.0.0.1:{}", HTTP_PORT);
		} catch (IOException e) {
			LOGGER.error("[DiscordWhitelist] Failed to start internal HTTP API on port {}: {}", HTTP_PORT, e.getMessage());
		}
	}

	private synchronized void stopHttpServer() {
		if (httpServer != null) {
			LOGGER.info("[DiscordWhitelist] Stopping internal HTTP API...");
			httpServer.stop(1);
			httpServer = null;
		}
	}

	private static void sendResponse(HttpExchange exchange, int statusCode, String responseText) throws IOException {
		byte[] bytes = responseText.getBytes(StandardCharsets.UTF_8);
		exchange.getResponseHeaders().set("Content-Type", "application/json; charset=UTF-8");
		exchange.sendResponseHeaders(statusCode, bytes.length);
		try (OutputStream os = exchange.getResponseBody()) {
			os.write(bytes);
		}
	}

	private static class StatusHandler implements HttpHandler {
		@Override
		public void handle(HttpExchange exchange) throws IOException {
			JsonObject json = new JsonObject();
			json.addProperty("status", "ok");
			json.addProperty("server_running", minecraftServer != null);
			if (minecraftServer != null) {
				json.addProperty("players_online", minecraftServer.getPlayerCount());
				json.addProperty("max_players", minecraftServer.getMaxPlayers());
			}
			sendResponse(exchange, 200, json.toString());
		}
	}

	private static class GetConsoleLogsHandler implements HttpHandler {
		@Override
		public void handle(HttpExchange exchange) throws IOException {
			if (!"GET".equalsIgnoreCase(exchange.getRequestMethod())) {
				sendResponse(exchange, 405, "{\"error\":\"Method not allowed\"}");
				return;
			}

			JsonArray array = new JsonArray();
			String line;
			int count = 0;
			while ((line = DiscordConsoleAppender.LOG_QUEUE.poll()) != null && count < 200) {
				array.add(line);
				count++;
			}

			JsonObject response = new JsonObject();
			response.addProperty("status", "ok");
			response.add("logs", array);
			sendResponse(exchange, 200, response.toString());
		}
	}

	private static class ExecuteConsoleCommandHandler implements HttpHandler {
		@Override
		public void handle(HttpExchange exchange) throws IOException {
			if (!"POST".equalsIgnoreCase(exchange.getRequestMethod())) {
				sendResponse(exchange, 405, "{\"error\":\"Method not allowed\"}");
				return;
			}

			if (minecraftServer == null) {
				sendResponse(exchange, 503, "{\"error\":\"Minecraft server not ready\"}");
				return;
			}

			String body;
			try (InputStream is = exchange.getRequestBody()) {
				body = new String(is.readAllBytes(), StandardCharsets.UTF_8);
			}

			String command = "";
			try {
				JsonObject json = JsonParser.parseString(body).getAsJsonObject();
				if (json.has("command")) {
					command = json.get("command").getAsString().trim();
				}
			} catch (Exception e) {
				sendResponse(exchange, 400, "{\"error\":\"Invalid JSON body\"}");
				return;
			}

			if (command.isEmpty()) {
				sendResponse(exchange, 400, "{\"error\":\"Missing 'command' parameter\"}");
				return;
			}

			if (command.startsWith("/")) {
				command = command.substring(1).trim();
			}

			final String finalCmd = command;
			minecraftServer.execute(() -> {
				try {
					LOGGER.info("[ConsoleBridge] Executing command: {}", finalCmd);
					minecraftServer.getCommands().performPrefixedCommand(
							minecraftServer.createCommandSourceStack(),
							finalCmd
					);
				} catch (Exception e) {
					LOGGER.error("[ConsoleBridge] Error executing command '{}': {}", finalCmd, e.getMessage());
				}
			});

			JsonObject json = new JsonObject();
			json.addProperty("status", "success");
			json.addProperty("command", finalCmd);
			sendResponse(exchange, 200, json.toString());
		}
	}

	private static class ReloadWhitelistHandler implements HttpHandler {
		@Override
		public void handle(HttpExchange exchange) throws IOException {
			if (minecraftServer == null) {
				sendResponse(exchange, 503, "{\"error\":\"Minecraft server not ready\"}");
				return;
			}

			minecraftServer.execute(() -> {
				try {
					minecraftServer.getPlayerList().reloadWhiteList();
					LOGGER.info("[DiscordWhitelist] Whitelist reloaded via API.");
				} catch (Exception e) {
					LOGGER.error("[DiscordWhitelist] Error reloading whitelist: {}", e.getMessage());
				}
			});

			JsonObject json = new JsonObject();
			json.addProperty("status", "success");
			json.addProperty("message", "Whitelist reloaded");
			sendResponse(exchange, 200, json.toString());
		}
	}

	private static class AddWhitelistHandler implements HttpHandler {
		@Override
		public void handle(HttpExchange exchange) throws IOException {
			if (!"POST".equalsIgnoreCase(exchange.getRequestMethod())) {
				sendResponse(exchange, 405, "{\"error\":\"Method not allowed\"}");
				return;
			}

			if (minecraftServer == null) {
				sendResponse(exchange, 503, "{\"error\":\"Minecraft server not ready\"}");
				return;
			}

			String body;
			try (InputStream is = exchange.getRequestBody()) {
				body = new String(is.readAllBytes(), StandardCharsets.UTF_8);
			}

			String name = "";
			try {
				JsonObject json = JsonParser.parseString(body).getAsJsonObject();
				if (json.has("name")) {
					name = json.get("name").getAsString().trim();
				}
			} catch (Exception e) {
				sendResponse(exchange, 400, "{\"error\":\"Invalid JSON body\"}");
				return;
			}

			if (name.isEmpty()) {
				sendResponse(exchange, 400, "{\"error\":\"Missing 'name' parameter\"}");
				return;
			}

			final String finalName = name;
			minecraftServer.execute(() -> {
				try {
					minecraftServer.getCommands().performPrefixedCommand(
							minecraftServer.createCommandSourceStack(),
							"whitelist add " + finalName
					);
					LOGGER.info("[DiscordWhitelist] Added '{}' to server whitelist via API.", finalName);
				} catch (Exception e) {
					LOGGER.error("[DiscordWhitelist] Error adding player '{}': {}", finalName, e.getMessage());
				}
			});

			JsonObject json = new JsonObject();
			json.addProperty("status", "success");
			json.addProperty("player", name);
			sendResponse(exchange, 200, json.toString());
		}
	}
}
