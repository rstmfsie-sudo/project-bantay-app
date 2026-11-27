// The frontend's title-bar buttons call window.pywebview.api.* (its original desktop shell); same API here.
const { contextBridge, ipcRenderer } = require("electron");
const call = (fn) => () => ipcRenderer.send("win", fn);
contextBridge.exposeInMainWorld("pywebview", {
  api: { minimize: call("minimize"), toggle_maximize: call("toggle_maximize"), close: call("close") },
});
