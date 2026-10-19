// Held-key tracking via DOM events (so typing in inputs never moves the player).
import { isTyping } from "../ui/dom";

const down = new Set<string>();

window.addEventListener("keydown", (e) => {
  if (isTyping()) return;
  down.add(e.code);
});
window.addEventListener("keyup", (e) => down.delete(e.code));
window.addEventListener("blur", () => down.clear());

export function clearKeys() {
  down.clear();
}

export function axis(): [number, number] {
  if (isTyping()) return [0, 0];
  let dx = 0;
  let dy = 0;
  if (down.has("KeyA") || down.has("ArrowLeft")) dx -= 1;
  if (down.has("KeyD") || down.has("ArrowRight")) dx += 1;
  if (down.has("KeyW") || down.has("ArrowUp")) dy -= 1;
  if (down.has("KeyS") || down.has("ArrowDown")) dy += 1;
  return [dx, dy];
}

export function isDown(code: string): boolean {
  return down.has(code);
}
