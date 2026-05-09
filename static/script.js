const chatForm = document.getElementById('chat-form');
const userInput = document.getElementById('user-input');
const chatContainer = document.getElementById('chat-container');
const typingIndicator = document.getElementById('typing-indicator');

function addMessage(text, isUser = false) {
    if (text === null || text === undefined) text = "";
    text = String(text);
    
    // Remove welcome message if it's the first message
    const welcome = document.querySelector('.welcome-message');
    if (welcome) welcome.remove();

    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${isUser ? 'user' : 'agent'}`;
    
    // Simple markdown-like replacement for bold and code blocks
    let formattedText = text
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
        .replace(/\n/g, '<br>');

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

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value, { stream: true });
            const lines = chunk.split('\n');
            
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
                    }
                } catch(e) {
                    console.error("JSON parse error on line:", line, e);
                }
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
