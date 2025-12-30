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
