const siteHeader = document.querySelector(".site-header");
const navToggle = siteHeader?.querySelector(".nav-toggle");

if (siteHeader && navToggle) {
	const navigation = siteHeader.querySelector(".site-nav");
	const firstNavigationLink = navigation?.querySelector("a");

	const closeNavigation = (restoreFocus = false) => {
		siteHeader.classList.remove("nav-open");
		document.body.classList.remove("nav-scroll-lock");
		navToggle.setAttribute("aria-expanded", "false");
		navToggle.textContent = "Menu";
		navToggle.setAttribute("aria-label", "Open navigation menu");
		if (restoreFocus) navToggle.focus();
	};

	navToggle.addEventListener("click", () => {
		const isOpen = siteHeader.classList.toggle("nav-open");
		document.body.classList.toggle("nav-scroll-lock", isOpen);
		navToggle.setAttribute("aria-expanded", String(isOpen));
		navToggle.textContent = isOpen ? "Close" : "Menu";
		navToggle.setAttribute("aria-label", isOpen ? "Close navigation menu" : "Open navigation menu");
		if (isOpen) firstNavigationLink?.focus();
	});

	siteHeader.querySelectorAll(".site-nav a").forEach(link => link.addEventListener("click", () => closeNavigation()));
	document.addEventListener("keydown", event => {
		if (event.key === "Escape") closeNavigation(true);
	});
}

const madeHoopsMain = document.querySelector(".made-hoops-page .case-main");

if (madeHoopsMain) {
	const madeHoopsSectionOrder = [
		"summer-title",
		"teams-title",
		"recruiting-title",
		"rankings-title",
		"templates-title",
		"youtube-title"
	];
	const sections = madeHoopsSectionOrder
		.map(titleId => madeHoopsMain.querySelector(`#${titleId}`)?.closest(".case-section"))
		.filter(Boolean);
	const allSections = [...madeHoopsMain.querySelectorAll(":scope > .case-section")];

	if (sections.length === allSections.length) {
		const fragment = document.createDocumentFragment();
		sections.forEach((section, index) => {
			const number = section.querySelector(".case-section-head .eyebrow");
			if (number) number.textContent = String(index + 1).padStart(2, "0");
			fragment.append(section);
		});
		madeHoopsMain.insertBefore(fragment, madeHoopsMain.querySelector(":scope > .footer-bottom"));
	}
}

const galleryImages = [...document.querySelectorAll("body.case-page img:not(.badgers-hero-mark):not(.made-hoops-hero-mark)")];

if (galleryImages.length) {
	const lightbox = document.createElement("dialog");
	lightbox.className = "image-lightbox";
	lightbox.setAttribute("aria-label", "Image viewer");

	const closeButton = document.createElement("button");
	closeButton.className = "image-lightbox-close";
	closeButton.type = "button";
	closeButton.setAttribute("aria-label", "Close image viewer");
	closeButton.title = "Close image viewer";

	const closeIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
	closeIcon.setAttribute("viewBox", "0 0 24 24");
	closeIcon.setAttribute("aria-hidden", "true");
	const closePath = document.createElementNS("http://www.w3.org/2000/svg", "path");
	closePath.setAttribute("d", "M6 6l12 12M18 6L6 18");
	closeIcon.append(closePath);
	closeButton.append(closeIcon);

	const figure = document.createElement("figure");
	const expandedImage = document.createElement("img");
	expandedImage.alt = "";
	const caption = document.createElement("figcaption");
	figure.append(expandedImage, caption);
	lightbox.append(closeButton, figure);
	document.body.append(lightbox);

	let activeImage = null;

	const openLightbox = image => {
		activeImage = image;
		expandedImage.src = image.currentSrc || image.src;
		expandedImage.alt = image.alt;
		caption.textContent = image.closest("figure")?.querySelector("figcaption")?.textContent.trim() || image.alt;
		caption.hidden = !caption.textContent;
		lightbox.showModal();
		closeButton.focus();
	};

	galleryImages.forEach(image => {
		image.classList.add("lightbox-trigger");
		image.tabIndex = 0;
		image.setAttribute("role", "button");
		image.setAttribute("aria-haspopup", "dialog");
		image.setAttribute("aria-label", image.alt ? `Enlarge image: ${image.alt}` : "Enlarge image");
	});

	document.addEventListener("click", event => {
		const image = event.target instanceof Element ? event.target.closest("img.lightbox-trigger") : null;
		if (!image) return;
		event.preventDefault();
		event.stopPropagation();
		openLightbox(image);
	});

	document.addEventListener("keydown", event => {
		if (lightbox.open && event.key === "Escape") {
			event.preventDefault();
			lightbox.close();
			return;
		}

		const image = event.target instanceof Element ? event.target.closest("img.lightbox-trigger") : null;
		if (!image || (event.key !== "Enter" && event.key !== " ")) return;
		event.preventDefault();
		openLightbox(image);
	});

	closeButton.addEventListener("click", () => lightbox.close());
	lightbox.addEventListener("click", event => {
		if (event.target === lightbox) lightbox.close();
	});
	lightbox.addEventListener("close", () => {
		if (activeImage?.isConnected) activeImage.focus();
		activeImage = null;
		expandedImage.removeAttribute("src");
	});
}
