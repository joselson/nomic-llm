import { mountBars } from "./bars";
import { mountDebug } from "./debug";
import { mountFeed } from "./feed";
import { mountHud } from "./hud";
import { mountMechHud } from "./mechHud";
import { mountModals } from "./modals";
import { mountNight } from "./night";

/** Mount every in-game overlay; returns a cleanup function. */
export function mountWorldUI(): () => void {
  const parts = [mountMechHud(), mountBars(), mountFeed(), mountHud(), mountNight(), mountModals(), mountDebug()];
  return () => parts.forEach((f) => f());
}
