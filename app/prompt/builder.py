class PromptBuilder:
    def build(self, system_prompt, profile, history, message):
        content = system_prompt + "\n\n" + f"User Profile:\n{profile}"

        messages = [
            {"role": "system", "content": content}
        ]

        messages.extend(history)
        messages.append({"role": "user", "content": message})
        return messages
