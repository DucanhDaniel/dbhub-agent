const chatForm = document.getElementById('chat-form');
const userInput = document.getElementById('user-input');
const chatContainer = document.getElementById('chat-container');
const typingIndicator = document.getElementById('typing-indicator');

let sessionTokens = 0;
let sessionCost = 0.0;

function addMessage(text, isUser = false) {
    if (text === null || text === undefined) text = "";
    text = String(text);
    
    // Remove welcome message if it's the first message
    const welcome = document.querySelector('.welcome-message');
    if (welcome) welcome.remove();

    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${isUser ? 'user' : 'agent'}`;
    
    // Render full markdown using marked.js
    const renderer = new marked.Renderer();
    // Links open in new tab
    renderer.link = function(href, title, text) {
        // marked v14+ passes an object; older versions pass positional args
        if (typeof href === 'object') {
            const token = href;
            return `<a href="${token.href}" target="_blank" rel="noopener">${token.text}</a>`;
        }
        return `<a href="${href}" target="_blank" rel="noopener">${text}</a>`;
    };
    let formattedText = marked.parse(text, { renderer, breaks: true });

    messageDiv.innerHTML = formattedText;
    chatContainer.appendChild(messageDiv);
    
    // Scroll to bottom
    chatContainer.scrollTo({
        top: chatContainer.scrollHeight,
        behavior: 'smooth'
    });
}

function sendSuggestion(text) {
    userInput.value = text;
    chatForm.dispatchEvent(new Event('submit'));
}

chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = userInput.value.trim();
    if (!message) return;

    addMessage(message, true);
    userInput.value = '';
    userInput.disabled = true;

    // Show typing indicator
    typingIndicator.style.display = 'block';
    chatContainer.scrollTo({ top: chatContainer.scrollHeight, behavior: 'smooth' });

    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message })
        });
        
        typingIndicator.style.display = 'none';
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        
        // Tạo container chứa log cho message này
        const logContainer = document.createElement('div');
        logContainer.className = 'logs-container';
        chatContainer.appendChild(logContainer);

        let finalMessage = "";
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\0');
            buffer = lines.pop(); // keep the last incomplete line in the buffer
            
            for (const line of lines) {
                if (!line.trim()) continue;
                try {
                    const data = JSON.parse(line);
                    if (data.type === 'log') {
                        const logLine = document.createElement('div');
                        logLine.className = 'log-line';
                        logLine.textContent = data.message;
                        logContainer.appendChild(logLine);
                        chatContainer.scrollTo({ top: chatContainer.scrollHeight, behavior: 'smooth' });
                    } else if (data.type === 'message') {
                        finalMessage = data.content;
                    } else if (data.type === 'usage') {
                        sessionTokens += data.tokens;
                        sessionCost += data.cost;
                        document.getElementById('session-stats').style.display = 'block';
                        document.getElementById('token-count').textContent = sessionTokens.toLocaleString();
                        document.getElementById('session-cost').textContent = '$' + sessionCost.toFixed(5);
                    }
                } catch(e) {
                    console.error("JSON parse error on line:", line, e);
                }
            }
        }
        
        // Process any remaining data in the buffer
        if (buffer.trim()) {
            try {
                const data = JSON.parse(buffer);
                if (data.type === 'message') {
                    finalMessage = data.content;
                }
            } catch(e) {
                console.error("JSON parse error on final buffer:", buffer, e);
            }
        }
        
        if (finalMessage) {
            addMessage(finalMessage);
        }
    } catch (error) {
        typingIndicator.style.display = 'none';
        addMessage('Xin lỗi, có lỗi xảy ra khi kết nối với máy chủ.');
        console.error(error);
    } finally {
        userInput.disabled = false;
        userInput.focus();
    }
});
