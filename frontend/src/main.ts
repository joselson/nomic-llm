import Phaser from "phaser";
import "./style.css";
import { JoinScene } from "./scenes/JoinScene";
import { LobbyScene } from "./scenes/LobbyScene";
import { WorldScene } from "./scenes/WorldScene";
import { wireStore } from "./state";

wireStore();

new Phaser.Game({
  type: Phaser.AUTO,
  parent: "game",
  backgroundColor: "#ffffff",
  scale: { mode: Phaser.Scale.RESIZE, width: window.innerWidth, height: window.innerHeight },
  render: { antialias: true, roundPixels: false },
  scene: [JoinScene, LobbyScene, WorldScene],
  disableContextMenu: true,
});
