import Image from "next/image";
import Link from "next/link";

import kmzaIcon from "../../public/kmza-icon.png";

const links = [
  { href: "/", label: "Chat" },
  { href: "/logged-out", label: "Logged Out" },
];

export function NavBar({ currentPath }: { currentPath?: string }) {
  return (
    <nav className="mb-8 flex items-start justify-between gap-6 border-b border-kw-elevated pb-4">
      <div className="flex items-center gap-6">
        <span className="font-heading text-xs font-bold uppercase tracking-widest text-kw-primary">
          {{name}}
        </span>
        <div className="flex gap-4">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={`text-xs font-mono uppercase tracking-wide transition-colors duration-200 ${
                currentPath === link.href
                  ? "border-b border-kw-primary text-kw-primary"
                  : "text-kw-text-secondary hover:text-kw-primary"
              }`}
            >
              {currentPath === link.href ? `> ${link.label}` : link.label}
            </Link>
          ))}
        </div>
      </div>
      <Image
        src={kmzaIcon}
        alt="Kamiwaza icon"
        width={22}
        height={22}
        priority
        className="h-5 w-5 rounded object-cover shadow-glow-teal sm:h-[22px] sm:w-[22px]"
      />
    </nav>
  );
}
