import "./noDevtools";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, NavLink, Outlet, Route, Routes } from "react-router-dom";
import "agentglow/style.css";
import "./app.css";
import Search from "./pages/Search";
import Backend from "./pages/Backend";

function Shell() {
  return (
    <>
      <nav className="nav">
        <span className="brand"><i />NYC Rides</span>
        <NavLink to="/" end>Search</NavLink>
        <NavLink to="/backend">Backend</NavLink>
      </nav>
      <Outlet />
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <BrowserRouter>
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Search />} />
        <Route path="backend" element={<Backend />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  </BrowserRouter>,
);
