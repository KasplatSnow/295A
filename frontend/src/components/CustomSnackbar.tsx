import { Snackbar, Alert } from "@mui/material";
import { useState } from "react";

export default function useSnackbar() {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [severity, setSeverity] = useState<"success" | "error" | "info" | "warning">("info");

  const showSnackbar = (msg: string, type: typeof severity = "info") => {
    setMessage(msg);
    setSeverity(type);
    setOpen(true);
  };

  const SnackbarComponent = (
    <Snackbar
      open={open}
      autoHideDuration={3000}
      onClose={() => setOpen(false)}
      anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
    >
      <Alert
        severity={severity}
        variant="filled"
        onClose={() => setOpen(false)}
        sx={{ width: "100%" }}
      >
        {message}
      </Alert>
    </Snackbar>
  );

  return { SnackbarComponent, showSnackbar };
}
