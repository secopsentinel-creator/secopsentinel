document.addEventListener("change", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement) || input.type !== "file") return;

  const targetId = input.dataset.filenameTarget;
  if (!targetId) return;

  const filename = document.getElementById(targetId);
  if (filename) {
    filename.textContent = input.files?.[0]?.name || "Ningún archivo seleccionado";
  }
});
