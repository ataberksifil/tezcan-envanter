/* Tezcan Envanter — small progressive enhancements. Every page works without this file. */
(function () {
    "use strict";

    // Anything that starts like a TZ1 label is resolved by the server, which
    // explains malformed, unknown or unauthorized codes. Other text is a search.
    var TZ1_PREFIX_RE = /^TZ1/i;

    function csrfToken() {
        try {
            return JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRFToken"] || "";
        } catch (e) {
            return "";
        }
    }

    function showSearchMessage(form, input, text) {
        var msg = form.querySelector("[data-scan-message]");
        if (msg) {
            msg.textContent = text;
        }
        input.setCustomValidity(text);
        input.reportValidity();
        input.addEventListener("input", function clear() {
            input.setCustomValidity("");
            if (msg) {
                msg.textContent = "";
            }
            input.removeEventListener("input", clear);
        });
    }

    // One shared "scan or search" behaviour for the top bar and the home page.
    // The scanner's Enter only ever resolves or searches; it never touches stock.
    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (!form.matches("[data-scan-search]")) {
            return;
        }
        var input = form.querySelector("input[name=q]");
        var value = input ? input.value.trim() : "";
        if (!value) {
            if (form.hasAttribute("data-require-query")) {
                event.preventDefault();
                input.focus();
            }
            return;
        }
        var resolveUrl = form.getAttribute("data-resolve-url");
        if (TZ1_PREFIX_RE.test(value) && resolveUrl) {
            event.preventDefault();
            var post = document.createElement("form");
            post.method = "post";
            post.action = resolveUrl;
            [["csrfmiddlewaretoken", csrfToken()], ["payload", value]].forEach(function (pair) {
                var field = document.createElement("input");
                field.type = "hidden";
                field.name = pair[0];
                field.value = pair[1];
                post.appendChild(field);
            });
            document.body.appendChild(post);
            post.submit();
            return;
        }
        if (form.hasAttribute("data-scan-only")) {
            event.preventDefault();
            showSearchMessage(form, input, "Bu alan yalnız TZ1 etiketlerini tanır. Etiketi okutun.");
        }
    });

    // Narrow screens get the short hint so the field stays understandable.
    var narrow = window.matchMedia("(max-width: 575.98px)");
    function applyPlaceholders() {
        document.querySelectorAll("input[data-short-placeholder]").forEach(function (input) {
            if (!input.hasAttribute("data-long-placeholder")) {
                input.setAttribute("data-long-placeholder", input.placeholder);
            }
            input.placeholder = narrow.matches
                ? input.getAttribute("data-short-placeholder")
                : input.getAttribute("data-long-placeholder");
        });
    }
    applyPlaceholders();
    if (narrow.addEventListener) {
        narrow.addEventListener("change", applyPlaceholders);
    }

    // "/" focuses the shared search field unless the user is already typing somewhere.
    document.addEventListener("keydown", function (event) {
        if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) {
            return;
        }
        var active = document.activeElement;
        if (active && (active.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName))) {
            return;
        }
        var target = document.querySelector("[data-primary-search]") || document.getElementById("global-search");
        if (target) {
            event.preventDefault();
            target.focus();
            target.select();
        }
    });

    // Decisions that change stock ask once more in the browser. The server
    // remains the authority; this only guards against a stray click.
    document.addEventListener("submit", function (event) {
        var form = event.target;
        var message = form.getAttribute && form.getAttribute("data-confirm");
        if (message && !window.confirm(message)) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    }, true);

    // After a rejected scan the field keeps the bad code selected, so the next
    // HID scan replaces it instead of being appended to it.
    var rejectedScan = document.querySelector("#scan-resolve-form .has-error input");
    if (rejectedScan) {
        rejectedScan.focus();
        rejectedScan.select();
    }

    // Double-submit guard for POST forms. Buttons are disabled after the browser
    // has captured the submitter value, so intent buttons keep working.
    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (event.defaultPrevented || (form.method || "").toLowerCase() !== "post" || form.hasAttribute("data-no-busy")) {
            return;
        }
        var submitter = event.submitter;
        window.setTimeout(function () {
            form.querySelectorAll("button[type=submit], input[type=submit]").forEach(function (button) {
                button.disabled = true;
            });
            if (submitter) {
                submitter.setAttribute("aria-busy", "true");
            }
        }, 0);
    });

    // Restore buttons when the page comes back from the back/forward cache.
    window.addEventListener("pageshow", function (event) {
        if (!event.persisted) {
            return;
        }
        document.querySelectorAll("form button[type=submit][disabled], form input[type=submit][disabled]").forEach(function (button) {
            button.disabled = false;
            button.removeAttribute("aria-busy");
        });
    });

    // Scan assist: decode a canonical TZ1 label locally and select the matching
    // option. Same UUID the server already renders; no request, no mutation.
    var KIND_LABEL = { M: "malzeme", A: "tekil varlık", L: "konum" };

    function payloadToUuid(value) {
        var match = /^TZ1([MAL]):([A-Za-z0-9_-]{22})$/.exec(value.trim());
        if (!match) {
            return null;
        }
        var b64 = match[2].replace(/-/g, "+").replace(/_/g, "/") + "==";
        var raw;
        try {
            raw = window.atob(b64);
        } catch (e) {
            return null;
        }
        if (raw.length !== 16) {
            return null;
        }
        var hex = "";
        for (var i = 0; i < raw.length; i += 1) {
            hex += ("0" + raw.charCodeAt(i).toString(16)).slice(-2);
        }
        return {
            kind: match[1],
            uuid: [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-"),
        };
    }

    function applyScanAssist(input) {
        var value = input.value.trim();
        var select = document.getElementById(input.getAttribute("data-scan-select"));
        var msg = document.getElementById(input.id + "-msg");
        if (!value || !select) {
            return false;
        }
        var expected = input.getAttribute("data-scan-kind");
        var decoded = payloadToUuid(value);
        var option = null;
        var error = "";
        if (!decoded) {
            error = "Okunan değer bir TZ1 etiketi değil. Listeden seçebilirsiniz.";
        } else if (expected && decoded.kind !== expected) {
            error = "Bu bir " + KIND_LABEL[decoded.kind] + " etiketi; burada " + KIND_LABEL[expected] + " etiketi bekleniyor.";
        } else {
            option = Array.prototype.find.call(select.options, function (opt) {
                return opt.value.toLowerCase() === decoded.uuid;
            });
            if (!option) {
                error = "Bu " + KIND_LABEL[decoded.kind] + " bu işlem için listede yok (pasif veya uygun değil).";
            }
        }
        if (msg) {
            msg.textContent = error || "Seçildi: " + option.text;
            msg.setAttribute("data-state", error ? "error" : "ok");
        }
        input.classList.remove("scan-flash-ok", "scan-flash-error");
        input.classList.add(error ? "scan-flash-error" : "scan-flash-ok");
        if (!error) {
            select.value = option.value;
            select.dispatchEvent(new Event("change", { bubbles: true }));
            input.value = "";
        } else {
            input.select();
        }
        return !error;
    }

    document.addEventListener("change", function (event) {
        if (event.target.matches && event.target.matches("[data-scan-select]")) {
            applyScanAssist(event.target);
        }
    });

    // Count sheets: Enter jumps to the next count field; after the last one it
    // focuses the save button so saving stays an explicit action.
    document.addEventListener("keydown", function (event) {
        var field = event.target;
        if (event.key !== "Enter" || !field.matches || !field.matches("[data-enter-next]") || !field.form) {
            return;
        }
        event.preventDefault();
        var fields = Array.prototype.slice.call(field.form.querySelectorAll("[data-enter-next]"));
        var next = fields[fields.indexOf(field) + 1];
        if (next) {
            next.focus();
            next.select();
        } else {
            var save = field.form.querySelector("button[type=submit]:not([form])");
            if (save) {
                save.focus();
            }
        }
    });

    // HID scanners end with Enter. In fields marked data-scan-field Enter moves to the
    // next field instead of submitting, so a scan never confirms an operation by itself.
    document.addEventListener("keydown", function (event) {
        var field = event.target;
        if (event.key !== "Enter" || !field.matches || !field.matches("[data-scan-field]")) {
            return;
        }
        event.preventDefault();
        if (field.hasAttribute("data-scan-select")) {
            if (applyScanAssist(field)) {
                field = document.getElementById(field.getAttribute("data-scan-select"));
            } else {
                return;
            }
        }
        var form = field.form;
        if (!form) {
            return;
        }
        var focusable = Array.prototype.filter.call(form.elements, function (el) {
            return !el.disabled && el.type !== "hidden" && el.offsetParent !== null;
        });
        var next = focusable[focusable.indexOf(field) + 1];
        if (next && next.type !== "submit") {
            next.focus();
        }
    });
})();
