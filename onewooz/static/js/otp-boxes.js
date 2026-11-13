/* Segmented one-time-code entry.
 *
 * Progressive enhancement for templates/accounts/_otp_field.html: a row of
 * square, single-digit boxes standing in for one text field. Typing a digit
 * advances to the next box, Backspace retreats, and the assembled value is
 * mirrored into the hidden `code` input that the form actually submits.
 *
 * Pasting is deliberately blocked. A paste (or drag-drop) into any box is
 * cancelled and an inline "Pasting is not allowed." warning is shown, so a
 * code can only be keyed in by hand. With scripting off, the <noscript> plain
 * field in the template keeps the flow usable.
 */
(function () {
  "use strict";

  function initGroup(root) {
    var boxes = Array.prototype.slice.call(root.querySelectorAll(".otp__box"));
    var hidden = root.querySelector('input[type="hidden"][data-otp-code]');
    var warn = root.querySelector("[data-otp-warn]");
    if (!boxes.length || !hidden) {
      return;
    }

    var warnTimer = null;

    function sync() {
      var value = "";
      for (var i = 0; i < boxes.length; i++) {
        value += boxes[i].value;
        boxes[i].classList.toggle("otp__box--filled", Boolean(boxes[i].value));
      }
      hidden.value = value;
    }

    function warnPasteBlocked() {
      if (!warn) {
        return;
      }
      warn.hidden = false;
      if (warnTimer) {
        clearTimeout(warnTimer);
      }
      warnTimer = setTimeout(function () {
        warn.hidden = true;
      }, 4000);
    }

    function blockPaste(event) {
      event.preventDefault();
      warnPasteBlocked();
    }

    boxes.forEach(function (box, index) {
      box.addEventListener("input", function () {
        // Keep only a single digit  the last one typed if several arrived.
        var digits = box.value.replace(/\D/g, "");
        box.value = digits ? digits.charAt(digits.length - 1) : "";
        if (box.value && index < boxes.length - 1) {
          boxes[index + 1].focus();
        }
        sync();
      });

      box.addEventListener("keydown", function (event) {
        if (event.key === "Backspace" && !box.value && index > 0) {
          boxes[index - 1].focus();
        } else if (event.key === "ArrowLeft" && index > 0) {
          event.preventDefault();
          boxes[index - 1].focus();
        } else if (event.key === "ArrowRight" && index < boxes.length - 1) {
          event.preventDefault();
          boxes[index + 1].focus();
        }
      });

      // Select the box's contents on focus so a keystroke overwrites cleanly.
      box.addEventListener("focus", function () {
        box.select();
      });

      // The requirement: pasting is not allowed. Cancel it and warn.
      box.addEventListener("paste", blockPaste);
      box.addEventListener("drop", blockPaste);
    });

    // Belt and braces: make sure the hidden field is current at submit time
    // even if an event was missed.
    var form = root.closest("form");
    if (form) {
      form.addEventListener("submit", sync);
    }

    sync();
  }

  function initAll() {
    var groups = document.querySelectorAll("[data-otp]");
    Array.prototype.slice.call(groups).forEach(initGroup);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
})();
