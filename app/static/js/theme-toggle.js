document.addEventListener("DOMContentLoaded", function () {
  const storageKey = "ikea-dashboard-theme";
  const root = document.documentElement;
  const toggle = document.getElementById("themeToggle");

  if (!toggle || !root) return;

  const icon = toggle.querySelector("[data-theme-icon]");
  const label = toggle.querySelector("[data-theme-label]");

  function getCurrentTheme() {
    return root.getAttribute("data-bs-theme") === "dark" ? "dark" : "light";
  }

  function setTheme(theme) {
    root.setAttribute("data-bs-theme", theme);
    try {
      localStorage.setItem(storageKey, theme);
    } catch (e) {
      // Ignore localStorage errors (e.g. in private mode)
    }

    if (icon) {
      icon.classList.remove("bi-moon-stars", "bi-sun");
      icon.classList.add(theme === "dark" ? "bi-sun" : "bi-moon-stars");
    }

    if (label) {
      label.textContent = theme === "dark" ? "Light" : "Dark";
    }

    toggle.setAttribute(
      "aria-label",
      theme === "dark" ? "Switch to light mode" : "Switch to dark mode"
    );
  }

  // Initial sync with theme decided in <head> script
  setTheme(getCurrentTheme());

  toggle.addEventListener("click", function () {
    const next = getCurrentTheme() === "dark" ? "light" : "dark";
    setTheme(next);
  });
});
