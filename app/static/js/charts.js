document.addEventListener("DOMContentLoaded", function () {
  const canvas = document.getElementById("stockHistoryChart");
  if (!canvas || !window.ITEM_HISTORY) return;

  const ctx = canvas.getContext("2d");

  const labels = window.ITEM_HISTORY.labels || [];
  let datasets = window.ITEM_HISTORY.datasets || [];

  // Backward compatibility: old "Total stock" format
  if ((!datasets || datasets.length === 0) && window.ITEM_HISTORY.stock) {
    datasets = [{
      label: "Total stock",
      data: window.ITEM_HISTORY.stock
    }];
  }

  // Nice palette for multiple lines (Chart.js defaults would all look identical)
  const palette = [
    "#0d6efd", "#20c997", "#fd7e14", "#6f42c1",
    "#198754", "#dc3545", "#0dcaf0", "#adb5bd"
  ];

  datasets = datasets.map((ds, i) => ({
    ...ds,
    borderColor: palette[i % palette.length],
    backgroundColor: palette[i % palette.length],
    fill: false,
    tension: 0.15,
    pointRadius: 2,
    pointHoverRadius: 4,
  }));

  // Keep original label generator so we can wrap it
  const baseGenerateLabels =
    Chart.defaults.plugins.legend.labels.generateLabels;

  const chart = new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: "bottom",
          // 1) Only generate legend entries for *visible* datasets
          labels: {
            generateLabels(chart) {
              const all = baseGenerateLabels(chart);
              return all.filter(label => {
                const meta = chart.getDatasetMeta(label.datasetIndex);
                return !meta.hidden; // hidden datasets => no label at all
              });
            }
          },
          // 2) Disable default click toggling – we control visibility via checkboxes
          onClick() {
            // no-op
          }
        },
        tooltip: { enabled: true }
      },
      scales: {
        x: {
          ticks: { maxRotation: 45, minRotation: 45 }
        },
        y: {
          beginAtZero: true
        }
      }
    }
  });

  // ----- Store selection (checkbox UI, max 3) -----------------------

  const checkboxes = document.querySelectorAll(".store-filter-checkbox");
  const initialSelectedFromServer =
    (window.ITEM_HISTORY && window.ITEM_HISTORY.selected_store_ids) || [];

  function applyStoreSelection(selectedIds) {
    const ids = new Set((selectedIds || []).map(String));
    let anyStoreDataset = false;

    chart.data.datasets.forEach((ds, index) => {
      const storeId = ds.store_id != null ? String(ds.store_id) : null;
      if (!storeId) {
        // Non-store datasets (e.g. fallback "Total stock") – always visible
        return;
      }
      anyStoreDataset = true;

      const meta = chart.getDatasetMeta(index);

      if (ids.size === 0) {
        // If nothing selected, show everything (or first 3 – handled at checkbox level)
        meta.hidden = false;
      } else {
        meta.hidden = !ids.has(storeId);
      }
    });

    if (anyStoreDataset) {
      chart.update();
    }
  }

  function getSelectedIdsFromCheckboxes() {
    return Array.from(checkboxes)
      .filter(cb => cb.checked)
      .map(cb => cb.value);
  }

  if (checkboxes.length) {
    // Initial selection comes from which boxes the server marked as checked
    let selected = getSelectedIdsFromCheckboxes();

    // Safety: if somehow more than 3 ended up checked, clamp to 3
    if (selected.length > 3) {
      selected = selected.slice(0, 3);
      checkboxes.forEach(cb => {
        cb.checked = selected.includes(cb.value);
      });
    }

    // If server didn't pre-check anything, fall back to selected_store_ids from JS
    if (selected.length === 0 && initialSelectedFromServer.length) {
      const wanted = initialSelectedFromServer.map(String);
      checkboxes.forEach(cb => {
        cb.checked = wanted.includes(cb.value);
      });
      selected = getSelectedIdsFromCheckboxes();
    }

    applyStoreSelection(selected);

    checkboxes.forEach(cb => {
      cb.addEventListener("change", function () {
        let current = getSelectedIdsFromCheckboxes();

        // Enforce max 3
        if (current.length > 3 && cb.checked) {
          cb.checked = false;          // undo this tick
          current = getSelectedIdsFromCheckboxes(); // recalc
        }

        applyStoreSelection(current);
      });
    });
  } else if (initialSelectedFromServer.length) {
    // No checkbox UI (≤ 3 stores) – still honour default selection
    applyStoreSelection(initialSelectedFromServer.map(String));
  }
});
