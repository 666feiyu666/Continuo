import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Garden } from "./garden/Garden";
import "./garden/garden.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Garden />
  </StrictMode>,
);
