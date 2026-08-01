// No framework. Four behaviours: theme toggle, the one-box composer (paste an
// image, grow with the text), infinite scroll, and click-to-play video.

const root = document.documentElement;

const currentTheme = () =>
  root.dataset.theme ||
  (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");

document.querySelector(".theme")?.addEventListener("click", () => {
  const next = currentTheme() === "light" ? "dark" : "light";
  root.dataset.theme = next;
  try {
    localStorage.setItem("blob-theme", next);
  } catch (e) {}
});

/* confirmation, in the page rather than in a browser alert */
const dialog = document.getElementById("confirm");
let pendingForm = null;

document.addEventListener("submit", (event) => {
  const form = event.target.closest("form[data-confirm]");
  // The second pass, after the dialog said yes, must go through untouched.
  if (!dialog || !form || form.dataset.confirmed) return;

  event.preventDefault();
  dialog.querySelector(".modal-text").textContent = form.dataset.confirm;
  pendingForm = form;
  dialog.showModal();
});

// Hung off the button rather than the dialog's close event: the button fires
// synchronously, so the form submits in the same task as the click.
dialog?.querySelector('button[value="ok"]').addEventListener("click", () => {
  const form = pendingForm;
  pendingForm = null;
  if (!form) return;
  form.dataset.confirmed = "1";
  form.requestSubmit();
});

// Cancel, Escape, or a click on the backdrop all mean no.
dialog?.addEventListener("close", () => {
  pendingForm = null;
});

/* composer: one box per page section (the feed, or a blob's sub-blobs) */
document.querySelectorAll(".composer").forEach((composer) => {
  const textarea = composer.querySelector("textarea");
  const fileInput = composer.querySelector("input[type=file]");
  const strip = composer.querySelector(".attachments");

  // The attachments this blob will carry. This list is the source of truth,
  // not fileInput.files: picking from the file dialog REPLACES that FileList
  // outright, so anything pasted earlier would vanish if we read it back.
  let attached = [];

  const identity = (file) => `${file.name}:${file.size}:${file.lastModified}`;

  // A FileList is read-only; DataTransfer is the only way to build one, which
  // is how the input ends up carrying everything that was pasted and picked.
  const sync = () => {
    const bag = new DataTransfer();
    attached.forEach((file) => bag.items.add(file));
    fileInput.files = bag.files;
    render();
  };

  const add = (files) => {
    const seen = new Set(attached.map(identity));
    attached = [
      ...attached,
      ...files.filter((file) => !seen.has(identity(file)) && seen.add(identity(file))),
    ];
    sync();
  };

  const render = () => {
    strip.querySelectorAll("img").forEach((img) => URL.revokeObjectURL(img.src));
    strip.replaceChildren();

    attached.forEach((file, index) => {
      const item = document.createElement("div");
      item.className = "attachment";

      const img = document.createElement("img");
      img.src = URL.createObjectURL(file);
      img.alt = file.name;

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "icon-btn tiny drop-attachment";
      remove.ariaLabel = `Remove ${file.name}`;
      remove.innerHTML =
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
      remove.addEventListener("click", () => {
        attached = attached.filter((_, i) => i !== index);
        sync();
      });

      item.append(img, remove);
      strip.append(item);
    });
  };

  // Textarea grows with its content instead of scrolling in a 2-line window.
  const grow = () => {
    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight}px`;
  };

  textarea.addEventListener("input", grow);

  // The dialog hands back only what was picked this time, so it is merged in
  // rather than taken as the whole list. Assigning fileInput.files fires no
  // change event, so sync() cannot loop back through here.
  fileInput.addEventListener("change", () => add([...fileInput.files]));

  // Paste screenshots straight into the box. The clipboard hands over Files,
  // which join whatever is already attached rather than replacing it.
  textarea.addEventListener("paste", (event) => {
    const pasted = [...(event.clipboardData?.items || [])]
      .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
      .map((item) => item.getAsFile())
      .filter(Boolean);
    if (!pasted.length) return;

    event.preventDefault();
    // Clipboard images are all named "image.png"; keep the extension, make the
    // name unique enough to tell apart in the admin.
    add(
      pasted.map(
        (file, index) =>
          new File(
            [file],
            `pasted-${Date.now()}-${index}.${file.type.split("/")[1] || "png"}`,
            { type: file.type }
          )
      )
    );
  });

  // Ctrl/Cmd+Enter sends, like every other box you paste things into.
  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      composer.requestSubmit();
    }
  });

  grow();
});

/* infinite scroll */
const feed = document.getElementById("feed");

if (feed) {
  let loading = false;

  const observer = new IntersectionObserver(async (entries) => {
    const entry = entries[0];
    if (!entry.isIntersecting || loading) return;

    const sentinel = entry.target;
    loading = true;
    observer.unobserve(sentinel);

    try {
      // X-Partial makes the view return cards only, without the page shell.
      const response = await fetch(sentinel.dataset.next, {
        headers: { "X-Partial": "1" },
      });
      if (!response.ok) throw new Error(response.status);
      sentinel.insertAdjacentHTML("beforebegin", await response.text());
      sentinel.remove();
      watchSentinel();
    } catch (err) {
      // Leave the sentinel in place so a scroll retries after a blip.
      observer.observe(sentinel);
    } finally {
      loading = false;
    }
  }, { rootMargin: "400px" });

  const watchSentinel = () => {
    const sentinel = feed.querySelector(".sentinel");
    if (sentinel) observer.observe(sentinel);
  };

  watchSentinel();
}

/* lightbox: full-size image over the page, not a new one */
const lightbox = document.getElementById("lightbox");

if (lightbox) {
  const frame = lightbox.querySelector("img");

  document.addEventListener("click", (event) => {
    const link = event.target.closest("a.full-shot");
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey) return;

    event.preventDefault();
    frame.src = link.href;
    lightbox.showModal();
  });

  // Clicking the dialog itself means clicking beside the image, since the
  // image and the close button are the only things inside it. Escape is
  // handled by <dialog> already.
  const shut = () => {
    lightbox.close();
    frame.removeAttribute("src");
  };

  lightbox.addEventListener("click", (event) => {
    if (event.target === lightbox || event.target.closest(".close")) shut();
  });

  // Escape closes a <dialog> on its own, without going through shut().
  lightbox.addEventListener("close", () => frame.removeAttribute("src"));
}

/* click to play */
document.addEventListener("click", (event) => {
  const player = event.target.closest(".player");
  if (!player) return;

  event.preventDefault();
  const iframe = document.createElement("iframe");
  iframe.src = `${player.dataset.embed}?autoplay=1`;
  iframe.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
  iframe.allowFullscreen = true;
  iframe.referrerPolicy = "no-referrer";
  player.replaceWith(iframe);
});
