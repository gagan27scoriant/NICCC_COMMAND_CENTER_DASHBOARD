import sys

with open('templates/dashboard.html', 'r', encoding='utf-8') as f:
    content = f.read()

html_replacement = """
            <div class="city-body" id="city-body">
              <div class="city-main" style="flex: 1; display: flex; flex-direction: column; gap: 8px; min-width: 0; min-height: 0; overflow: auto;">
                <div class="city-grid" id="area-list"></div>
                
                <!-- Chatbot Interface for City Screen -->
                <div class="chatbot-container resizable-panel" id="city-chatbot-wrap" onclick="activateCityChatMode()">
                  <div class="chatbot-header">
                    <div class="chatbot-avatar">
                      <span class="ai-status-dot"></span>
                      <span class="avatar-text">AI</span>
                    </div>
                    <div class="chatbot-header-text">
                      <div class="chatbot-title">GENIH Command Assistant</div>
                      <div class="chatbot-subtitle">Ready to query surveillance telemetry</div>
                    </div>
                    <div class="chatbot-actions" id="city-chat-header-actions" style="display: none;">
                      <button class="chat-clear-btn" onclick="deactivateCityChatMode()">Grid View</button>
                      <button class="chat-clear-btn" onclick="clearCityChat()">Clear</button>
                    </div>
                  </div>
                  <div class="chat-messages" id="city-chat-messages">
                    <div class="chat-message bot">
                      <div class="message-bubble">
                        Hello! I am the GENIH Command Assistant. You can ask me to analyze active alerts, query camera status, or check live telemetry. How can I help you today?
                      </div>
                    </div>
                  </div>
                  <div class="chat-quick-suggestions">
                    <button class="suggest-btn" onclick="suggestCityPrompt('Summarize all active threats in this city')">Summarize active threats</button>
                    <button class="suggest-btn" onclick="suggestCityPrompt('Show camera breakdown by status')">Camera breakdown</button>
                    <button class="suggest-btn" onclick="suggestCityPrompt('Check traffic congestion trends')">Traffic trends</button>
                  </div>
                  <div class="chat-input-area">
                    <input type="text" placeholder="Ask GENIH anything..." id="city-chatbot-input" onfocus="activateCityChatMode()" onkeydown="if(event.key==='Enter') sendCityChatPrompt()" />
                    <button class="chatbot-send-btn" onclick="sendCityChatPrompt()">Send</button>
                  </div>
                </div>
              </div>
              
              <div class="resize-divider" data-target="city-side"></div>
              
              <div class="city-side" id="city-side" style="width: 380px; min-width: 200px; max-width: 60%; display: flex; flex-direction: column; gap: 8px; min-height: 0; flex-shrink: 0;">
                <div class="stack-card live-alert-panel resizable-panel" id="city-alert-wrap" style="width: 100%; flex: 1; min-height: 180px;">
                  <div class="stack-head">Live Alerts</div>
                  <div class="panel-subtitle">City-scoped alerts and status updates.</div>
                  <div class="alert-log" id="city-alert-log"></div>
                </div>
                
                <div class="stack-card resizable-panel" id="area-quick-wrap" style="display: none; width: 100%; flex: 1; min-height: 0; overflow: hidden; flex-direction: column;">
                  <div class="stack-head">Quick Navigation</div>
                  <div class="panel-subtitle">Access zones quickly.</div>
                  <div class="city-quick-grid" id="area-quick-list" style="overflow-y: auto; padding-right: 4px; margin-top: 8px; display: flex; flex-direction: column; gap: 8px;"></div>
                </div>
              </div>
            </div>
"""

original_city_body = """            <div class="city-body" id="city-body">
              <div class="city-grid" id="area-list"></div>
              <div class="resize-divider" data-target="city-alert-wrap"></div>
              <div class="stack-card live-alert-panel resizable-panel" id="city-alert-wrap">
                <div class="stack-head">Live Alerts</div>
                <div class="panel-subtitle">City-scoped alerts and status updates.</div>
                <div class="alert-log" id="city-alert-log"></div>
              </div>
            </div>"""

content = content.replace(original_city_body, html_replacement)

# Add CSS rules
css_replacement = """    .city-body.chat-active #area-list {
      display: none;
    }

    .city-body.chat-active #area-quick-wrap {
      display: flex !important;
    }

    /* Resize divider between panels */"""

content = content.replace("    /* Resize divider between panels */", css_replacement)


# Add JS code for City Chat
js_code = """
    // --- CITY CHATBOT FUNCTIONS ---

    function renderAreaQuickList() {
      const list = $('area-quick-list');
      if (!list) return;
      const areas = getAreasForCity(state.city);
      list.innerHTML = areas.map((area) => {
        const isActive = area.name === state.area ? 'active' : '';
        const tone = getAreaTone(area);
        const chipText = area.status === 'Alert' ? 'Alert' : area.status;
        return `
      <div class="city-quick-card ${isActive} ${tone}" data-area="${area.name}">
        <div class="city-quick-top">
          <div class="city-quick-name">${area.name}</div>
          <span class="city-quick-chip ${tone}">${chipText}</span>
        </div>
        <div class="city-quick-meta">${area.cameras} cameras · ${area.alertCriteria}</div>
      </div>
        `;
      }).join('');
      
      list.querySelectorAll('.city-quick-card').forEach(card => {
        card.addEventListener('click', () => {
          openArea(card.dataset.area);
        });
      });
    }

    function activateCityChatMode() {
      const body = $('city-body');
      if (body && !body.classList.contains('chat-active')) {
        body.classList.add('chat-active');
        $('city-chat-header-actions').style.display = 'flex';
        renderAreaQuickList();
      }
    }

    function deactivateCityChatMode() {
      const body = $('city-body');
      if (body && body.classList.contains('chat-active')) {
        body.classList.remove('chat-active');
        $('city-chat-header-actions').style.display = 'none';
        renderAreaQuickList();
      }
    }

    function suggestCityPrompt(text) {
      $('city-chatbot-input').value = text;
      activateCityChatMode();
      sendCityChatPrompt();
    }

    function clearCityChat() {
      const messages = $('city-chat-messages');
      messages.innerHTML = `
    <div class="chat-message bot">
      <div class="message-bubble">
        Hello! I am the GENIH Command Assistant. You can ask me to analyze active alerts, query camera status, or check live telemetry. How can I help you today?
      </div>
    </div>
  `;
      deactivateCityChatMode();
    }

    function sendCityChatPrompt() {
      const input = $('city-chatbot-input');
      const val = input.value.trim();
      if (!val) return;

      const messages = $('city-chat-messages');
      
      messages.insertAdjacentHTML('beforeend', `
        <div class="chat-message user">
          <div class="message-bubble">${val}</div>
        </div>
      `);
      
      input.value = '';
      messages.scrollTop = messages.scrollHeight;

      setTimeout(() => {
        messages.insertAdjacentHTML('beforeend', `
          <div class="chat-message bot">
            <div class="message-bubble">${getBotReplyText(val)}</div>
          </div>
        `);
        messages.scrollTop = messages.scrollHeight;
      }, 600);
      
      sendPrompt(`GENIH query in City View for ${state.city}: ${val}`);
    }

"""

content = content.replace("    function getBotReplyText(query) {", js_code + "\n    function getBotReplyText(query) {")

with open('templates/dashboard.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated city screen chatbot!")
