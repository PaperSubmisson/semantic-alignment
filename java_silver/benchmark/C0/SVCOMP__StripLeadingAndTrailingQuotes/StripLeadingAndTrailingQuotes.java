class StripLeadingAndTrailingQuotes {

    static String stripLeadingAndTrailingQuotes(String str) {
        boolean temp_Boolean = false;
        temp_Boolean = str.startsWith("\"");
        if (temp_Boolean)
        {
            str = str.substring(1, str.length());
        }
        temp_Boolean = str.endsWith("\"");
        if (temp_Boolean)
        {
            str = str.substring(0, str.length() - 1);
        }
        return str;
    }
    
}