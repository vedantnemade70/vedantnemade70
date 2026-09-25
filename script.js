(function () {
  // Footer year
  document.getElementById("year").textContent = new Date().getFullYear();

  // Sticky nav shadow
  var nav = document.getElementById("nav");
  window.addEventListener("scroll", function () {
    nav.classList.toggle("is-scrolled", window.scrollY > 10);
  }, { passive: true });

  // Mobile menu
  var toggle = document.getElementById("navToggle");
  var links = document.getElementById("navLinks");
  toggle.addEventListener("click", function () {
    var open = links.classList.toggle("is-open");
    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
  });
  links.querySelectorAll("a").forEach(function (a) {
    a.addEventListener("click", function () {
      links.classList.remove("is-open");
      toggle.setAttribute("aria-expanded", "false");
    });
  });

  // Project filters
  var filters = document.querySelectorAll(".filter");
  var projects = document.querySelectorAll(".project");
  filters.forEach(function (btn) {
    btn.addEventListener("click", function () {
      filters.forEach(function (b) { b.classList.remove("is-active"); });
      btn.classList.add("is-active");
      var f = btn.dataset.filter;
      projects.forEach(function (p) {
        p.classList.toggle("is-hidden", f !== "all" && p.dataset.type !== f);
      });
    });
  });

  // Count-up numbers
  function countUp(el) {
    var target = parseFloat(el.dataset.count);
    var decimals = parseInt(el.dataset.decimals || "0", 10);
    var start = null;
    var duration = 1600;
    function step(ts) {
      if (!start) start = ts;
      var t = Math.min((ts - start) / duration, 1);
      var eased = 1 - Math.pow(1 - t, 3);
      el.textContent = (target * eased).toFixed(decimals);
      if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  if (!("IntersectionObserver" in window)) {
    document.documentElement.classList.add("no-js");
    document.querySelectorAll("[data-count]").forEach(function (el) {
      el.textContent = parseFloat(el.dataset.count).toFixed(parseInt(el.dataset.decimals || "0", 10));
    });
    document.querySelector(".skills").classList.add("is-visible");
    return;
  }

  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      var el = entry.target;
      if (el.dataset.count) countUp(el);
      el.classList.add("is-visible");
      io.unobserve(el);
    });
  }, { threshold: 0.2 });

  document.querySelectorAll(".reveal, [data-count], .skills").forEach(function (el) { io.observe(el); });

  // Highlight current section in nav
  var sections = document.querySelectorAll("section[id]");
  var navLinks = document.querySelectorAll('.nav__links a[href^="#"]:not(.btn)');
  var spy = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      navLinks.forEach(function (a) {
        a.classList.toggle("is-current", a.getAttribute("href") === "#" + entry.target.id);
      });
    });
  }, { rootMargin: "-45% 0px -50% 0px" });
  sections.forEach(function (s) { spy.observe(s); });
})();
