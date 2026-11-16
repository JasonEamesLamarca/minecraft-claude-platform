package dev.mcai.mod;

/**
 * Defensive cleanup of text shown in chat. The gateway already sanitizes replies; this is a second layer so
 * that nothing from outside the server can inject formatting codes or control characters into chat.
 * Text is always displayed as a literal, never parsed as JSON text components or commands.
 */
public final class ChatText {
    public static final int MAX_LENGTH = 2000;

    private ChatText() {
    }

    public static String clean(String in) {
        if (in == null) {
            return "";
        }
        StringBuilder out = new StringBuilder(Math.min(in.length(), MAX_LENGTH));
        for (int i = 0; i < in.length() && out.length() < MAX_LENGTH; i++) {
            char c = in.charAt(i);
            if (c == '§') {          // Minecraft formatting code prefix: drop it and the code char
                i++;
                continue;
            }
            if (c == '\n' || c == '\t') {
                out.append(' ');
            } else if (!Character.isISOControl(c) && Character.getType(c) != Character.FORMAT) {
                out.append(c);
            }
        }
        return out.toString().replaceAll(" {2,}", " ").strip();
    }
}
