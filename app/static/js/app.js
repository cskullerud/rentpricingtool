// Prevents double submits (each valuation is saved, and may cost a data-plan request).
document.addEventListener("submit", function (event) {
  var form = event.target;
  if (!form.hasAttribute || !form.hasAttribute("data-single-submit")) return;
  if (form.dataset.submitted) { event.preventDefault(); return; }
  form.dataset.submitted = "1";
  var button = form.querySelector("button[type=submit]");
  if (button) { button.disabled = true; button.textContent = "Working…"; }
});
// Coming back with the browser's Back button restores the page; make the form usable again.
window.addEventListener("pageshow", function (event) {
  if (!event.persisted) return;
  document.querySelectorAll("form[data-single-submit]").forEach(function (form) {
    delete form.dataset.submitted;
    var button = form.querySelector("button[type=submit]");
    if (button) { button.disabled = false; button.textContent = "Get valuation"; }
  });
});
