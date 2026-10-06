import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { Приложение } from "./App";
import "./styles.css";

const корень = document.getElementById("root");
if (!корень) {
  throw new Error("Не найден элемент root");
}

createRoot(корень).render(
  <StrictMode>
    <Приложение />
  </StrictMode>,
);
