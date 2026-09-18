import React from "react";
import ReactDOM from "react-dom/client";
import { App as AntApp, ConfigProvider, theme } from "antd";
import App from "./App";
import { PageErrorBoundary } from "./components/PageErrorBoundary";
import "antd/dist/reset.css";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><ConfigProvider theme={{ algorithm: theme.defaultAlgorithm, token: { borderRadius: 4, controlHeight: 32 } }}><AntApp><PageErrorBoundary><App /></PageErrorBoundary></AntApp></ConfigProvider></React.StrictMode>);
