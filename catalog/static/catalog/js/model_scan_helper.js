(function () {
    "use strict";

    const toggleButton = document.getElementById("model-scan-toggle");
    if (!toggleButton) return;

    const panel = document.getElementById("model-scan-panel");
    const startButton = document.getElementById("model-scan-start");
    const stopButton = document.getElementById("model-scan-stop");
    const videoWrap = document.getElementById("model-scan-video-wrap");
    const video = document.getElementById("model-scan-video");
    const status = document.getElementById("model-scan-status");
    const helper = document.getElementById("model-scan-helper");
    const applyButton = document.getElementById("model-scan-apply");
    const modelInput = document.querySelector("[data-model-scan-target]");
    const form = document.getElementById("material-form");
    let controls = null;

    function setStatus(message, isError) {
        status.textContent = message;
        status.classList.toggle("text-danger", Boolean(isError));
        status.classList.toggle("text-body-secondary", !isError);
    }

    function fillModel(value) {
        const text = (value || "").trim();
        if (!text) {
            setStatus("Kod okunamadı veya boş. Değeri elle girebilir veya düzeltebilirsiniz.", true);
            return false;
        }
        if (!modelInput) {
            setStatus("Model alanı bulunamadı.", true);
            return false;
        }
        modelInput.value = text;
        modelInput.dispatchEvent(new Event("input", { bubbles: true }));
        modelInput.focus();
        setStatus("Okunan değer model alanına yazıldı. Kaydetmeden önce düzeltebilirsiniz.", false);
        return true;
    }

    function stopScanner() {
        if (controls) {
            controls.stop();
            controls = null;
        }
        if (video.srcObject) {
            video.srcObject.getTracks().forEach(function (track) { track.stop(); });
            video.srcObject = null;
        }
        videoWrap.classList.add("d-none");
        stopButton.classList.add("d-none");
        startButton.classList.remove("d-none");
    }

    function friendlyCameraError(error) {
        if (error && (error.name === "NotAllowedError" || error.name === "SecurityError")) {
            return "Kamera izni verilmedi. USB okuyucu veya elle giriş kullanabilirsiniz.";
        }
        if (error && (error.name === "NotFoundError" || error.name === "DevicesNotFoundError")) {
            return "Kullanılabilir kamera bulunamadı. USB okuyucu veya elle giriş kullanabilirsiniz.";
        }
        return "Kamera başlatılamadı. Tekrar deneyin veya değeri elle girin.";
    }

    function buildReader() {
        const reader = new window.ZXingBrowser.BrowserMultiFormatReader(undefined, {
            delayBetweenScanAttempts: 200,
            delayBetweenScanSuccess: 1000
        });
        const formats = window.ZXingBrowser.BarcodeFormat;
        if (formats && formats.QR_CODE !== undefined && formats.CODE_128 !== undefined) {
            reader.possibleFormats = [formats.QR_CODE, formats.CODE_128];
        }
        return reader;
    }

    async function startScanner() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            setStatus("Bu tarayıcı kamera erişimini desteklemiyor. USB okuyucu veya elle giriş kullanın.", true);
            return;
        }
        if (!window.ZXingBrowser || !window.ZXingBrowser.BrowserMultiFormatReader) {
            setStatus("Tarayıcı bileşeni yüklenemedi. USB okuyucu veya elle giriş kullanın.", true);
            return;
        }

        startButton.classList.add("d-none");
        stopButton.classList.remove("d-none");
        videoWrap.classList.remove("d-none");
        setStatus("Kamera izni bekleniyor…", false);

        try {
            const reader = buildReader();
            controls = await reader.decodeFromConstraints(
                { audio: false, video: { facingMode: { ideal: "environment" } } },
                video,
                function (result, _error, callbackControls) {
                    if (!result) return;
                    fillModel(result.getText());
                    callbackControls.stop();
                    controls = null;
                    videoWrap.classList.add("d-none");
                    stopButton.classList.add("d-none");
                    startButton.classList.remove("d-none");
                }
            );
            setStatus("Üretici barkodunu kameraya gösterin. Form otomatik kaydedilmez.", false);
        } catch (error) {
            stopScanner();
            setStatus(friendlyCameraError(error), true);
        }
    }

    toggleButton.addEventListener("click", function () {
        panel.classList.toggle("d-none");
        if (!panel.classList.contains("d-none") && helper) {
            helper.focus();
        } else {
            stopScanner();
        }
    });
    startButton.addEventListener("click", startScanner);
    stopButton.addEventListener("click", function () {
        stopScanner();
        setStatus("Kamera durduruldu.", false);
    });
    applyButton.addEventListener("click", function () {
        fillModel(helper.value);
    });
    helper.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            fillModel(helper.value);
        }
    });
    if (form) {
        form.addEventListener("submit", function () {
            stopScanner();
        });
    }
    window.addEventListener("pagehide", stopScanner);
}());
