/**
 * API client for the PartSelect Chat Agent backend.
 *
 * Provides both streaming and non-streaming chat interfaces.
 */

const API_BASE_URL = process.env.REACT_APP_API_URL || "http://localhost:8000";

// Session management
let currentSessionId = null;
let currentSessionState = null;

/**
 * Get AI message (non-streaming).
 *
 * @param {string} userQuery - The user's question
 * @returns {Promise<{role: string, content: string}>} The assistant's response
 */
export const getAIMessage = async (userQuery) => {
  try {
    const response = await fetch(`${API_BASE_URL}/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        message: userQuery,
        session_id: currentSessionId,
        session_state: currentSessionState,
      }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to get response");
    }

    const data = await response.json();
    console.log("API Response:", data);
    console.log("Parts in response:", data.parts);

    // Update session
    currentSessionId = data.session_id;
    currentSessionState = data.session_state;

    return {
      role: "assistant",
      content: data.message,
      partCards: data.parts || [],
    };
  } catch (error) {
    console.error("Chat API error:", error);
    return {
      role: "assistant",
      content: `Sorry, I encountered an error: ${error.message}. Please try again.`,
    };
  }
};

/**
 * Get AI message with streaming.
 *
 * @param {string} userQuery - The user's question
 * @param {function} onToken - Callback for each token received
 * @param {function} onComplete - Callback when streaming is complete
 * @param {function} onError - Callback for errors
 * @returns {Promise<void>}
 */
export const getAIMessageStreaming = async (
  userQuery,
  onToken,
  onComplete,
  onError
) => {
  try {
    const response = await fetch(`${API_BASE_URL}/chat/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({
        message: userQuery,
        session_id: currentSessionId,
        session_state: currentSessionState,
      }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to get response");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let fullMessage = "";

    while (true) {
      const { done, value } = await reader.read();

      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Parse SSE events
      const lines = buffer.split("\n");
      buffer = lines.pop() || ""; // Keep incomplete line in buffer

      for (const line of lines) {
        if (line.startsWith("event:")) {
          // Skip event type line, data follows
          continue;
        }

        if (line.startsWith("data:")) {
          const data = line.slice(5).trim();

          if (!data) continue;

          try {
            const parsed = JSON.parse(data);

            if (parsed.token) {
              fullMessage += parsed.token;
              onToken(parsed.token);
            }

            if (parsed.message) {
              // Done event
              currentSessionId = parsed.session_id;
              currentSessionState = parsed.session_state;
              onComplete({
                role: "assistant",
                content: parsed.message || fullMessage,
                partCards: parsed.parts || [],
              });
              return;
            }

            if (parsed.error) {
              throw new Error(parsed.error);
            }
          } catch (e) {
            // Not JSON, might be raw text
            if (data !== "[DONE]") {
              console.warn("Failed to parse SSE data:", data);
            }
          }
        }
      }
    }

    // If we get here without a done event, complete with what we have
    onComplete({
      role: "assistant",
      content: fullMessage,
    });
  } catch (error) {
    console.error("Streaming API error:", error);
    if (onError) {
      onError(error);
    } else {
      onComplete({
        role: "assistant",
        content: `Sorry, I encountered an error: ${error.message}. Please try again.`,
      });
    }
  }
};

/**
 * Reset the current session.
 * Call this to start a fresh conversation.
 */
export const resetSession = () => {
  currentSessionId = null;
  currentSessionState = null;
};

/**
 * Get current session ID.
 * @returns {string|null}
 */
export const getSessionId = () => currentSessionId;

/**
 * Check if the backend is healthy.
 * @returns {Promise<{status: string, missing_config: string[]}>}
 */
export const checkHealth = async () => {
  try {
    const response = await fetch(`${API_BASE_URL}/health`);
    return await response.json();
  } catch (error) {
    return { status: "unreachable", error: error.message };
  }
};
