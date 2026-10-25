import React from "react";
import { createRoot } from "react-dom/client";
import "@openuidev/react-ui/defaults.css";
import "@openuidev/react-ui/components.css";
import "./app.css";
import App from "./App.jsx";

createRoot(document.getElementById("root")).render(<App />);
