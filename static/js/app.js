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
  const emailForm = document.querySelector("#email-form");
  const emailBody = document.querySelector("#email-body");
  const characterCount = document.querySelector("#email-character-count");
  const copyButton = document.querySelector("#copy-email");
  const copyButtonLabel = document.querySelector("#copy-button-label");
  const copyStatus = document.querySelector("#copy-status");
  const recipientEditor = document.querySelector("#recipient-editor");

  const recipientState = { to: [], cc: [] };
  const recipientElements = {
    to: {
      input: document.querySelector("#email-to-input"),
      list: document.querySelector("#email-to-list"),
      error: document.querySelector("#email-to-error"),
    },
    cc: {
      input: document.querySelector("#email-cc-input"),
      list: document.querySelector("#email-cc-list"),
      error: document.querySelector("#email-cc-error"),
    },
  };
  const hiddenFields = document.querySelector("#recipient-hidden-fields");

  const normalizeInitialRecipients = (value) => {
    if (Array.isArray(value)) return value;
    if (typeof value !== "string" || !value.trim()) return [];
    return value.split(/[\s,;]+/).filter(Boolean);
  };

  const parseInitialRecipients = (value) => {
    try {
      return normalizeInitialRecipients(JSON.parse(value));
    } catch {
      return [];
    }
  };

  const isValidEmail = (address) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(address);

  const setRecipientError = (kind, message = "") => {
    const { input, error } = recipientElements[kind];
    error.textContent = message;
    error.hidden = !message;
    if (message) {
      input.setAttribute("aria-invalid", "true");
    } else {
      input.removeAttribute("aria-invalid");
    }
  };

  const syncHiddenFields = () => {
    hiddenFields.replaceChildren();
    Object.entries(recipientState).forEach(([kind, addresses]) => {
      addresses.forEach((address) => {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = kind === "to" ? "email_to" : "email_cc";
        input.value = address;
        hiddenFields.append(input);
      });
    });
  };

  const renderRecipientList = (kind) => {
    const { list } = recipientElements[kind];
    list.replaceChildren();
    recipientState[kind].forEach((address) => {
      const item = document.createElement("li");
      item.className = "recipient-chip";

      const addressText = document.createElement("span");
      addressText.textContent = address;

      const removeButton = document.createElement("button");
      removeButton.type = "button";
      removeButton.className = "recipient-remove-button";
      removeButton.dataset.removeRecipient = kind;
      removeButton.dataset.address = address;
      removeButton.setAttribute("aria-label", `${address}を${kind === "to" ? "To" : "CC"}から削除`);
      removeButton.textContent = "×";

      item.append(addressText, removeButton);
      list.append(item);
    });
    syncHiddenFields();
  };

  const addRecipients = (kind, rawValue, announceError = true) => {
    const addresses = rawValue.split(/[\s,;]+/).filter(Boolean);
    if (!addresses.length) {
      if (announceError) setRecipientError(kind, "メールアドレスを入力してください。");
      return false;
    }

    const invalidAddress = addresses.find((address) => !isValidEmail(address));
    if (invalidAddress) {
      setRecipientError(kind, `「${invalidAddress}」はメールアドレスの形式が正しくありません。`);
      return false;
    }

    const registered = new Set([...recipientState.to, ...recipientState.cc].map((address) => address.toLowerCase()));
    const duplicateAddress = addresses.find((address, index) => {
      const normalized = address.toLowerCase();
      return registered.has(normalized) || addresses.findIndex((item) => item.toLowerCase() === normalized) !== index;
    });
    if (duplicateAddress) {
      setRecipientError(kind, `「${duplicateAddress}」はすでに追加されています。`);
      return false;
    }

    recipientState[kind].push(...addresses);
    recipientElements[kind].input.value = "";
    setRecipientError(kind);
    renderRecipientList(kind);
    return true;
  };

  if (recipientEditor) {
    recipientState.to = parseInitialRecipients(recipientEditor.dataset.initialTo);
    recipientState.cc = parseInitialRecipients(recipientEditor.dataset.initialCc)
      .filter((address) => !recipientState.to.some((toAddress) => toAddress.toLowerCase() === address.toLowerCase()));
    renderRecipientList("to");
    renderRecipientList("cc");

    document.querySelectorAll("[data-add-recipient]").forEach((button) => {
      button.addEventListener("click", () => {
        const kind = button.dataset.addRecipient;
        addRecipients(kind, recipientElements[kind].input.value);
      });
    });

    Object.entries(recipientElements).forEach(([kind, { input }]) => {
      input.addEventListener("input", () => setRecipientError(kind));
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === "," || event.key === ";") {
          event.preventDefault();
          addRecipients(kind, input.value);
        }
      });
    });

    recipientEditor.addEventListener("click", (event) => {
      const button = event.target.closest("[data-remove-recipient]");
      if (!button) return;
      const kind = button.dataset.removeRecipient;
      recipientState[kind] = recipientState[kind].filter((address) => address !== button.dataset.address);
      setRecipientError(kind);
      renderRecipientList(kind);
      recipientElements[kind].input.focus();
    });
  }

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

  emailForm.addEventListener("submit", (event) => {
    const pendingTo = recipientElements.to.input.value.trim();
    const pendingCc = recipientElements.cc.input.value.trim();
    const toIsValid = !pendingTo || addRecipients("to", pendingTo);
    const ccIsValid = !pendingCc || addRecipients("cc", pendingCc);

    if (!toIsValid || !ccIsValid || recipientState.to.length === 0) {
      event.preventDefault();
      if (recipientState.to.length === 0 && toIsValid) {
        setRecipientError("to", "Toの宛先を1件以上追加してください。");
      }
      (toIsValid ? recipientElements.cc.input : recipientElements.to.input).focus();
      return;
    }

    const subject = document.querySelector("#email-subject");
    if (!subject.value.trim() || !emailBody.value.trim()) {
      event.preventDefault();
      emailForm.reportValidity();
      return;
    }

    const sendButton = emailForm.querySelector('button[type="submit"]');
    if (sendButton) {
      sendButton.disabled = true;
      sendButton.textContent = "送信中…";
    }
  });

  updateCharacterCount();
}
