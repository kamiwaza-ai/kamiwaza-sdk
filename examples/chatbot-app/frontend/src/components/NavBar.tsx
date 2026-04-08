import Link from "next/link";

const links = [
  { href: "/", label: "Chat" },
  { href: "/logged-out", label: "Logged Out" },
];

export function NavBar({ currentPath }: { currentPath?: string }) {
  return (
    <nav className="flex items-center gap-6 border-b border-kw-elevated pb-4 mb-8">
      <span className="font-heading text-xs font-bold uppercase tracking-widest text-kw-primary">
        chatbot-app
      </span>
      <div className="flex gap-4">
        {links.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            className={`text-xs font-mono uppercase tracking-wide transition-colors duration-200 ${
              currentPath === link.href
                ? "text-kw-primary border-b border-kw-primary"
                : "text-kw-text-secondary hover:text-kw-primary"
            }`}
          >
            {currentPath === link.href ? `> ${link.label}` : link.label}
          </Link>
        ))}
      </div>
    </nav>
  );
}
