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

// Keep the "Search criteria" panel in step with the form, so what is shown is what will be used.
// The server renders the same text, so the page is correct without this script too.
(function () {
  function setText(id, text) {
    var element = document.getElementById(id);
    if (element) { element.textContent = text; }
  }
  function selectedSummary(id) {
    var select = document.getElementById(id);
    if (!select || select.selectedIndex < 0) { return null; }
    var option = select.options[select.selectedIndex];
    return option.getAttribute("data-summary") || option.textContent;
  }
  function refresh() {
    var address = document.getElementById("address");
    setText("summary-address", address && address.value.trim() ? address.value.trim() : "Not entered yet");
    [["search_radius_miles", "summary-radius"], ["lookback_days", "summary-lookback"],
     ["property_type", "summary-property_type"]].forEach(function (pair) {
      var text = selectedSummary(pair[0]);
      if (text) { setText(pair[1], text); }
    });
    var size = document.getElementById("summary-size");
    var sqft = document.getElementById("sqft");
    if (size && sqft) {
      size.textContent = sqft.value.trim() ? size.getAttribute("data-with") : size.getAttribute("data-without");
    }
  }
  document.addEventListener("input", refresh);
  document.addEventListener("change", refresh);
  window.addEventListener("pageshow", refresh);
})();
