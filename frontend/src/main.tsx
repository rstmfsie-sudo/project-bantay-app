import React from "react";
import ReactDOM from "react-dom/client";
// Fonts are bundled (not loaded from the web) so the app stays fully offline.
import "@fontsource/poppins/latin-400.css";
import "@fontsource/poppins/latin-500.css";
import "@fontsource/poppins/latin-600.css";
import "@fontsource/poppins/latin-700.css";
import "@fontsource/lora/latin-500-italic.css";
import "@fontsource/lora/latin-600-italic.css";
import "@fontsource/jetbrains-mono/latin-400.css";
import "@fontsource/jetbrains-mono/latin-500.css";
import "@fontsource/jetbrains-mono/latin-700.css";
import App from "./App";
import "./styles.css";

document.documentElement.dataset.theme = localStorage.getItem("theme") === "light" ? "light" : "dark";
ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
