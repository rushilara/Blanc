/** BLA-54: LLM-suggested follow-up questions. Clicking only fills the input; never auto-runs. */

type Props = {
  suggestions?: string[] | null;
  disabled?: boolean;
  onPick: (question: string) => void;
};

export function SuggestedFollowups({ suggestions, disabled, onPick }: Props) {
  if (!suggestions?.length) return null;
  return (
    <div className="blanc-suggestions">
      <p className="blanc-suggestions-label">Suggestions — not findings</p>
      <ul>
        {suggestions.map(question => (
          <li key={question}>
            <button type="button" disabled={disabled} onClick={() => onPick(question)}>
              {question}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
