(function () {
  'use strict';

  /* Mobile nav toggle */
  var toggle = document.querySelector('.nav-toggle');
  var scrim = document.querySelector('.nav-scrim');
  function closeNav() {
    document.body.classList.remove('nav-open');
    if (toggle) toggle.setAttribute('aria-expanded', 'false');
  }
  if (toggle) {
    toggle.addEventListener('click', function () {
      var open = document.body.classList.toggle('nav-open');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }
  if (scrim) scrim.addEventListener('click', closeNav);

  /* Mega-menu: click-to-toggle (works for touch, mouse, and keyboard;
     desktop also gets a CSS :hover reveal, this covers everything else). */
  var navItems = document.querySelectorAll('.nav-item');
  navItems.forEach(function (item) {
    var link = item.querySelector(':scope > a');
    var menu = item.querySelector(':scope > .mega-menu');
    if (!link || !menu) return;

    link.addEventListener('click', function (e) {
      var isMobile = window.matchMedia('(max-width: 980px)').matches;
      if (!isMobile) return;
      e.preventDefault();
      var willOpen = !item.classList.contains('is-open');
      navItems.forEach(function (other) { other.classList.remove('is-open'); });
      if (willOpen) item.classList.add('is-open');
    });
  });

  document.addEventListener('click', function (e) {
    if (!e.target.closest('.nav-item')) {
      navItems.forEach(function (item) { item.classList.remove('is-open'); });
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      navItems.forEach(function (item) { item.classList.remove('is-open'); });
      closeNav();
    }
  });

  /* FAQ accordion */
  document.querySelectorAll('.faq-item').forEach(function (item) {
    var q = item.querySelector('.faq-q');
    if (!q) return;
    q.addEventListener('click', function () {
      var isOpen = item.classList.contains('is-open');
      item.classList.toggle('is-open', !isOpen);
      q.setAttribute('aria-expanded', !isOpen ? 'true' : 'false');
    });
  });

  /* Quote-start field: placeholder submit handler until a real quoting
     engine or CRM integration is wired up. */
  document.querySelectorAll('.quote-start').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var input = form.querySelector('input');
      if (input && input.value.trim()) {
        window.location.href = form.dataset.action || '/get-a-quote/?address=' + encodeURIComponent(input.value.trim());
      }
    });
  });
})();
