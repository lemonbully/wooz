/* TinyMCE initialisation for the Django admin (marketing core pages + blog).
 *
 * Mounts the free TinyMCE 6 build over every <textarea class="admin-richtext">,
 * with the same toolbar/plugins as the owner dashboard editor. Inline images are
 * embedded as data URIs (paste + file picker), so no upload endpoint is needed.
 * New inline formset rows (subpages / child pages) are initialised as they are
 * added, and every editor is flushed back to its textarea before submit.
 */
(function () {
  "use strict";

  var CONFIG = {
    menubar: "edit view insert format tools table",
    plugins:
      "advlist autolink lists link image charmap preview anchor searchreplace visualblocks visualchars code fullscreen insertdatetime media table emoticons help wordcount",
    toolbar:
      "undo redo | blocks fontsize | bold italic underline strikethrough | forecolor backcolor | alignleft aligncenter alignright alignjustify | bullist numlist outdent indent | link image media table | blockquote hr | removeformat code fullscreen preview",
    height: 420,
    menubar_sticky: true,
    branding: false,
    promotion: false,
    license_key: "gpl",
    content_style:
      "body{font-family:Inter,system-ui,sans-serif;font-size:14px;line-height:1.6}",
    // Embed images inline (data URI)  no server upload endpoint required.
    paste_data_images: true,
    automatic_uploads: false,
    file_picker_types: "image",
    file_picker_callback: function (callback, value, meta) {
      if (meta.filetype !== "image") return;
      var input = document.createElement("input");
      input.setAttribute("type", "file");
      input.setAttribute("accept", "image/*");
      input.addEventListener("change", function () {
        var file = input.files && input.files[0];
        if (!file) return;
        var reader = new FileReader();
        reader.onload = function () {
          callback(reader.result, { title: file.name });
        };
        reader.readAsDataURL(file);
      });
      input.click();
    },
    setup: function (editor) {
      editor.on("change input undo redo keyup SetContent", function () {
        editor.save();
      });
    },
  };

  function initOne(ta) {
    if (!window.tinymce || !ta || ta.dataset.tmceReady === "1") return;
    // Skip the empty inline "template" row Django clones for new formset rows.
    if (ta.id && ta.id.indexOf("__prefix__") !== -1) return;
    ta.dataset.tmceReady = "1";
    window.tinymce.init(Object.assign({ target: ta }, CONFIG));
  }

  function initAll(scope) {
    var root = scope || document;
    root.querySelectorAll("textarea.admin-richtext").forEach(initOne);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initAll(document);

    // Django admin fires this on the row element when an inline row is added.
    document.addEventListener("formset:added", function (event) {
      if (event.target) initAll(event.target);
    });

    // Flush every editor back to its textarea before the form posts.
    document.querySelectorAll("form").forEach(function (form) {
      form.addEventListener("submit", function () {
        if (window.tinymce) window.tinymce.triggerSave();
      });
    });
  });
})();
