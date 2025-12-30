// Custom JavaScript for Kanyo Admin

/**
 * Auto-scroll function for log viewer
 */
function enableAutoScroll(elementId) {
    const element = document.getElementById(elementId);
    if (!element) return;

    const autoScrollCheckbox = document.getElementById('auto-scroll');
    if (!autoScrollCheckbox) return;

    if (autoScrollCheckbox.checked) {
        element.scrollTop = element.scrollHeight;
    }
}

/**
 * Initialize auto-scroll for log viewer
 */
document.addEventListener('DOMContentLoaded', () => {
    const autoScrollCheckbox = document.getElementById('auto-scroll');
    const logOutput = document.getElementById('log-output');

    if (autoScrollCheckbox && logOutput) {
        autoScrollCheckbox.addEventListener('change', () => {
            if (autoScrollCheckbox.checked) {
                enableAutoScroll('log-output');
            }
        });

        // Auto-scroll on content updates
        const observer = new MutationObserver(() => {
            enableAutoScroll('log-output');
        });

        observer.observe(logOutput, { childList: true, subtree: true });
    }
});

/**
 * HTMX event handlers
 */
document.addEventListener('htmx:afterSwap', (event) => {
    // Re-enable auto-scroll after content swap
    if (event.detail.target.id === 'log-output') {
        enableAutoScroll('log-output');
    }
});

// Show loading state for HTMX requests
document.addEventListener('htmx:beforeRequest', (event) => {
    const target = event.detail.target;
    if (target) {
        target.classList.add('htmx-loading');
    }
});

document.addEventListener('htmx:afterRequest', (event) => {
    const target = event.detail.target;
    if (target) {
        target.classList.remove('htmx-loading');
    }
});

// ─────────────────────────────────────────────────────────────────────────────
// Media Viewer Controls
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Get media viewer elements
 */
function getViewerElements() {
    return {
        liveFrame: document.getElementById('live-frame'),
        clipVideo: document.getElementById('clip-video'),
        clipImage: document.getElementById('clip-image'),
        liveBtn: document.getElementById('live-btn'),
        nowPlaying: document.getElementById('now-playing'),
        nowPlayingText: document.getElementById('now-playing-text')
    };
}

/**
 * Show YouTube live stream
 */
function showLive() {
    const els = getViewerElements();
    if (!els.liveFrame) return;

    // Hide clip viewers
    if (els.clipVideo) {
        els.clipVideo.classList.add('hidden');
        els.clipVideo.pause();
    }
    if (els.clipImage) {
        els.clipImage.classList.add('hidden');
    }

    // Show live
    els.liveFrame.classList.remove('hidden');
    if (els.liveBtn) {
        els.liveBtn.classList.add('bg-red-600');
        els.liveBtn.classList.remove('bg-zinc-600');
    }
    if (els.nowPlaying) {
        els.nowPlaying.classList.add('hidden');
    }
}

/**
 * Play a video clip
 * @param {string} url - URL of the video clip
 * @param {string} title - Title to display
 */
function playClip(url, title) {
    const els = getViewerElements();
    if (!els.clipVideo) return;

    // Hide live and image
    if (els.liveFrame) {
        els.liveFrame.classList.add('hidden');
    }
    if (els.clipImage) {
        els.clipImage.classList.add('hidden');
    }

    // Show and play video
    els.clipVideo.src = url;
    els.clipVideo.classList.remove('hidden');
    els.clipVideo.play();

    // Update UI
    if (els.liveBtn) {
        els.liveBtn.classList.remove('bg-red-600');
        els.liveBtn.classList.add('bg-zinc-600');
    }
    if (els.nowPlaying) {
        els.nowPlaying.classList.remove('hidden');
    }
    if (els.nowPlayingText) {
        els.nowPlayingText.textContent = title || url.split('/').pop();
    }
}

/**
 * Show an image
 * @param {string} url - URL of the image
 * @param {string} title - Title to display
 */
function showImage(url, title) {
    const els = getViewerElements();
    if (!els.clipImage) return;

    // Hide live and video
    if (els.liveFrame) {
        els.liveFrame.classList.add('hidden');
    }
    if (els.clipVideo) {
        els.clipVideo.classList.add('hidden');
        els.clipVideo.pause();
    }

    // Show image
    els.clipImage.src = url;
    els.clipImage.classList.remove('hidden');

    // Update UI
    if (els.liveBtn) {
        els.liveBtn.classList.remove('bg-red-600');
        els.liveBtn.classList.add('bg-zinc-600');
    }
    if (els.nowPlaying) {
        els.nowPlaying.classList.remove('hidden');
    }
    if (els.nowPlayingText) {
        els.nowPlayingText.textContent = title || url.split('/').pop();
    }
}

// Make functions globally available
window.showLive = showLive;
window.playClip = playClip;
window.showImage = showImage;

// ─────────────────────────────────────────────────────────────────────────────
// Local Stream Time Clock
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Update the local stream time based on timezone (IANA name or offset)
 */
function updateStreamTime() {
    const streamTimeEl = document.getElementById('stream-time');
    if (!streamTimeEl) return;

    // Get timezone from data attribute (set in template)
    const timezone = streamTimeEl.dataset.timezone;
    if (!timezone) return;

    let streamTime;

    // Try to use IANA timezone name if browser supports it
    if (timezone.includes('/') || timezone === 'UTC') {
        try {
            // Use Intl.DateTimeFormat with the stream's timezone
            const now = new Date();
            streamTime = new Date(now.toLocaleString('en-US', { timeZone: timezone }));
        } catch (e) {
            // Fallback to UTC if timezone not recognized
            console.warn(`Timezone ${timezone} not recognized, using UTC`);
            streamTime = new Date();
        }
    } else {
        // Parse timezone offset (e.g., "-05:00" or "+10:00") - legacy support
        const match = timezone.match(/([+-])(\d{2}):(\d{2})/);
        if (!match) return;

        const sign = match[1] === '+' ? 1 : -1;
        const hours = parseInt(match[2], 10);
        const minutes = parseInt(match[3], 10);
        const offsetMinutes = sign * (hours * 60 + minutes);

        // Calculate stream time
        const now = new Date();
        const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
        streamTime = new Date(utc + (offsetMinutes * 60000));
    }

    // Format as HH:MM:SS
    const hh = String(streamTime.getHours()).padStart(2, '0');
    const mm = String(streamTime.getMinutes()).padStart(2, '0');
    const ss = String(streamTime.getSeconds()).padStart(2, '0');

    streamTimeEl.textContent = `${hh}:${mm}:${ss}`;
}

// Update stream time every second
setInterval(updateStreamTime, 1000);
updateStreamTime(); // Initial update

// ─────────────────────────────────────────────────────────────────────────────
// Auto-hide success messages
// ─────────────────────────────────────────────────────────────────────────────

document.body.addEventListener('htmx:afterSwap', function(event) {
    if (event.target.id === 'save-feedback') {
        const successMsg = event.target.querySelector('.bg-green-600\\/20');
        if (successMsg) {
            setTimeout(() => {
                successMsg.style.transition = 'opacity 0.5s';
                successMsg.style.opacity = '0';
                setTimeout(() => successMsg.remove(), 500);
            }, 5000);
        }
    }
});
