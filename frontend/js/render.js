export const LAYERS = ["browser", "mouse", "timing", "network"];

export function renderLayerBars(container, layerScores) {
  container.replaceChildren(...LAYERS.map((layer) => {
    const value = layerScores?.[layer];
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = layer;
    const track = document.createElement("span");
    track.className = "bar-track";
    const fill = document.createElement("span");
    fill.className = "bar-fill";
    fill.style.width = `${typeof value === "number" ? Math.max(2, value) : 0}%`;
    track.append(fill);
    const label = document.createElement("strong");
    label.textContent = typeof value === "number" ? Math.round(value) : "n/a";
    item.append(name, track, label);
    return item;
  }));
}
