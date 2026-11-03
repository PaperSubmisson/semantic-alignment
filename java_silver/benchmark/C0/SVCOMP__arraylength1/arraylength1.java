class arraylength1 {
  public static int func(int size) {
    if (size < 0) return -1;
    int int_array[] = new int[size];

    if(int_array.length != size)
      return 0;
    return 1;
  }
}
