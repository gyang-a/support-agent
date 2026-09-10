import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { WorkspacePage } from "@/pages/workspace/WorkspacePage";
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<WorkspacePage />} />
        <Route path="/chat/:conversationId" element={<WorkspacePage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
