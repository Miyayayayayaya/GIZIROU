const inputPage = document.body.dataset.page === "input";
const reviewPage = document.body.dataset.page === "review";

if (inputPage) {
  const form = document.querySelector("#transcript-form");
  const transcript = document.querySelector("#transcript");
  const fileInput = document.querySelector("#transcript-file");
  const selectedFile = document.querySelector("#selected-file");
  const selectedFileName = document.querySelector("#selected-file-name");
  const removeFile = document.querySelector("#remove-file");
  const characterCount = document.querySelector("#character-count");
  const fieldError = document.querySelector("#transcript-error");
  const loadingOverlay = document.querySelector("#loading-overlay");
  const analyzeButton = document.querySelector("#analyze-button");
  const maxFileSize = 1024 * 1024;

  const updateCharacterCount = () => {
    characterCount.textContent = `${transcript.value.length.toLocaleString("ja-JP")}文字`;
  };

  const clearError = () => {
    fieldError.hidden = true;
    transcript.removeAttribute("aria-invalid");
  };

  const showError = (message) => {
    fieldError.textContent = message;
    fieldError.hidden = false;
    transcript.setAttribute("aria-invalid", "true");
  };

  transcript.addEventListener("input", () => {
    updateCharacterCount();
    if (transcript.value.trim()) clearError();
  });

  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (!file) return;

    selectedFile.hidden = false;
    selectedFileName.textContent = file.name;

    if (!file.name.toLowerCase().endsWith(".txt")) {
      showError("アップロードできるファイルは.txt形式のみです。");
      return;
    }
    if (file.size > maxFileSize) {
      showError("ファイルサイズは1MB以下にしてください。");
      return;
    }

    const reader = new FileReader();
    reader.addEventListener("load", () => {
      transcript.value = String(reader.result || "");
      updateCharacterCount();
      clearError();
    });
    reader.addEventListener("error", () => showError("ファイルを読み込めませんでした。"));
    reader.readAsText(file, "UTF-8");
  });

  removeFile.addEventListener("click", () => {
    fileInput.value = "";
    selectedFile.hidden = true;
    selectedFileName.textContent = "";
    clearError();
  });

  form.addEventListener("submit", (event) => {
    const hasText = transcript.value.trim().length > 0;
    const file = fileInput.files[0];
    const validFile = file && file.name.toLowerCase().endsWith(".txt") && file.size <= maxFileSize;

    if (!hasText && !validFile) {
      event.preventDefault();
      showError("文字起こしを入力するか、txtファイルを選択してください。");
      transcript.focus();
      return;
    }

    analyzeButton.disabled = true;
    loadingOverlay.classList.add("is-visible");
    loadingOverlay.setAttribute("aria-hidden", "false");
  });

  updateCharacterCount();
}

if (reviewPage) {
  const emailBody = document.querySelector("#email-body");
  const characterCount = document.querySelector("#email-character-count");
  const copyButton = document.querySelector("#copy-email");
  const copyButtonLabel = document.querySelector("#copy-button-label");
  const copyStatus = document.querySelector("#copy-status");

  const updateCharacterCount = () => {
    characterCount.textContent = `${emailBody.value.length.toLocaleString("ja-JP")}文字`;
  };

  const fallbackCopy = () => {
    emailBody.focus();
    emailBody.select();
    return document.execCommand("copy");
  };

  copyButton.addEventListener("click", async () => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(emailBody.value);
      } else if (!fallbackCopy()) {
        throw new Error("copy failed");
      }
      copyStatus.textContent = "メール本文をコピーしました。";
      copyButtonLabel.textContent = "コピーしました";
      window.setTimeout(() => {
        copyButtonLabel.textContent = "メール本文をコピー";
        copyStatus.textContent = "";
      }, 2400);
    } catch {
      copyStatus.textContent = "コピーできませんでした。本文を選択してコピーしてください。";
    }
  });

  emailBody.addEventListener("input", updateCharacterCount);
  updateCharacterCount();
}
