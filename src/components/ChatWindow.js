import React, { useState, useEffect, useRef } from "react";
import "./ChatWindow.css";
import { getAIMessage, resetSession } from "../api/api";
import { marked } from "marked";
import personIcon from "../assets/personicon.svg";
import PartCard from "./PartCard";

// Configure marked to open all links in new tabs
const renderer = new marked.Renderer();
const originalLinkRenderer = renderer.link.bind(renderer);
renderer.link = (href, title, text) => {
  const html = originalLinkRenderer(href, title, text);
  return html.replace(/^<a /, '<a target="_blank" rel="noopener noreferrer" ');
};
marked.setOptions({ renderer });

function ChatWindow() {

  const defaultMessage = [{
    role: "assistant",
    content: `Welcome! I'm your PartSelect Assistant for **refrigerator** and **dishwasher** parts.

I can help you with:
- **Finding parts** - Search by part number, model number, or describe what you need
- **Compatibility checks** - Verify if a part fits your specific model or if parts fit certain brands
- **Troubleshooting** - Diagnose issues like "ice maker not working" or "dishwasher won't drain"
- **Installation guidance** - Get difficulty ratings, time estimates, and video guides
- **Reviews, Q&A, and Repair Stories** - I can look at these for specific parts


I'm not equipped to give specific information about models, but I can tell you about part compatibility and general repair advice and trouble symptoms for relevant appliances. 

  
Try asking me something like:
- "Is part PS11752778 compatible with my WDT780SAEM1?"
- "The ice maker on my Whirlpool fridge is not working"
- "How do I install part PS11752778?"`
  }];

  const [messages,setMessages] = useState(defaultMessage)
  const [input, setInput] = useState("");
  const [isThinking, setIsThinking] = useState(false);
  const [thinkingTime, setThinkingTime] = useState(0);

  const messagesEndRef = useRef(null);
  const timerRef = useRef(null);

  const scrollToBottom = () => {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
      scrollToBottom();
  }, [messages, isThinking]);

  // Timer effect for thinking indicator
  useEffect(() => {
    if (isThinking) {
      setThinkingTime(0);
      timerRef.current = setInterval(() => {
        setThinkingTime(prev => prev + 1);
      }, 1000);
    } else {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }

    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
      }
    };
  }, [isThinking]);

  const handleSend = async (input) => {
    if (input.trim() !== "") {
      // Set user message
      setMessages(prevMessages => [...prevMessages, { role: "user", content: input }]);
      setInput("");

      // Show thinking indicator
      setIsThinking(true);

      // Call API & set assistant message
      const newMessage = await getAIMessage(input);
      console.log("New message object:", newMessage);
      console.log("partCards:", newMessage.partCards);

      // Hide thinking indicator
      setIsThinking(false);

      setMessages(prevMessages => [...prevMessages, newMessage]);
    }
  };

  const handleNewChat = () => {
    // Reset session on backend
    resetSession();
    // Reset messages to default
    setMessages(defaultMessage);
    // Clear input
    setInput("");
  };

  return (
      <div className="messages-container">
          {messages.map((message, index) => (
              <div key={index} className={`${message.role}-message-container`}>
                  {message.content && (
                      <>
                          {message.role === "user" && (
                              <div className="user-label">
                                  <span>User</span>
                                  <img src={personIcon} alt="" className="user-avatar" />
                              </div>
                          )}
                          <div className={`message ${message.role}-message`}>
                              <div dangerouslySetInnerHTML={{__html: marked(message.content).replace(/<p>|<\/p>/g, "")}}></div>
                              {/* Render PartCards if available for assistant messages */}
                              {message.role === "assistant" && message.partCards?.length > 0 && (
                                  <div className="part-cards-container">
                                      {message.partCards.map((part, i) => (
                                          <PartCard key={part.ps_number || i} part={part} />
                                      ))}
                                  </div>
                              )}
                          </div>
                      </>
                  )}
              </div>
          ))}
          {isThinking && (
            <div className="assistant-message-container">
              <div className="message assistant-message thinking-indicator">
                <div className="thinking-content">
                  <span className="thinking-dots">Thinking</span>
                  <span className="thinking-timer">{thinkingTime}s</span>
                </div>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
          <div className="ai-disclaimer">
            AI responses may contain errors. Always verify information on <a href="https://www.partselect.com" target="_blank" rel="noopener noreferrer">PartSelect.com</a>
          </div>
          <div className="input-area">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask me about refrigerator or dishwasher parts..."
              onKeyPress={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  handleSend(input);
                  e.preventDefault();
                }
              }}
              rows="3"
            />
            <div className="button-group">
              <button className="new-chat-button" onClick={handleNewChat}>
                New Chat
              </button>
              <button className="send-button" onClick={() => handleSend(input)}>
                Send
              </button>
            </div>
          </div>
      </div>
);
}

export default ChatWindow;
