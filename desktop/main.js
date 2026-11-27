// PROJECT B.A.N.T.A.Y. desktop shell: starts the bundled Python server and shows the app in a frameless window.
const { app, BrowserWindow, ipcMain, dialog } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const path = require("path");

const URL = "http://127.0.0.1:8765";
const SERVER = app.isPackaged
  ? path.join(process.resourcesPath, "bantay-server", "bantay-server.exe")
  : path.join(__dirname, "..", "app", "bantay-server", "bantay-server.exe");
let win, server;

const up = () => new Promise((done) => {
  const req = http.get(URL + "/api/status", (res) => { res.resume(); done(res.statusCode === 200); });
  req.on("error", () => done(false));
  req.setTimeout(1500, () => { req.destroy(); done(false); });
});

async function start() {
  if (!(await up())) {           // a server already on the port (e.g. run.py) is simply reused
    // stdin stays open as a pipe: when this app exits it closes, and the server shuts the lidar down cleanly
    server = spawn(SERVER, [], { windowsHide: true, stdio: ["pipe", "ignore", "ignore"] });
    server.on("exit", () => app.quit());
    server.on("error", (e) => { dialog.showErrorBox("PROJECT B.A.N.T.A.Y.", "Could not start the server:\n" + e.message); app.quit(); });
    let ok = false;
    for (let i = 0; i < 600 && !ok && server.exitCode === null; i++) {   // first start can take ~30 s
      ok = await up();
      if (!ok) await new Promise((r) => setTimeout(r, 100));
    }
    if (!ok) return;
  }
  win = new BrowserWindow({
    width: 1440, height: 900, minWidth: 960, minHeight: 640, frame: false, backgroundColor: "#1A1917",
    title: "PROJECT B.A.N.T.A.Y.", webPreferences: { preload: path.join(__dirname, "preload.js") },
  });
  win.webContents.on("did-finish-load", () => win.webContents.insertCSS(
    ".pywebview-drag-region{-webkit-app-region:drag}" +
    ".pywebview-drag-region button,.pywebview-drag-region .chip,.pywebview-drag-region .lamp{-webkit-app-region:no-drag}"));
  win.loadURL(URL);
}

ipcMain.on("win", (_, fn) => {
  if (!win) return;
  if (fn === "close") win.close();
  else if (fn === "minimize") win.minimize();
  else if (fn === "toggle_maximize") win.isMaximized() ? win.unmaximize() : win.maximize();
});

if (!app.requestSingleInstanceLock()) app.quit();   // one copy only: it owns COM6 and port 8765
else {
  app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
  app.whenReady().then(start);
  app.on("window-all-closed", () => app.quit());
  app.on("will-quit", () => server?.stdin?.end());
}
