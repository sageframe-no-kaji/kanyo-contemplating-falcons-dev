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
