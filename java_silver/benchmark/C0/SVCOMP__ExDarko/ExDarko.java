class MyInteger {
  int value;

  public MyInteger(int value) {
    this.value = value;
  }

  @Override
  public boolean equals(Object obj) {
    if (obj == this) {
      return true;
    }
    if (!(obj instanceof MyInteger)) {
      return false;
    }
    MyInteger other = (MyInteger) obj;
    return this.value == other.value;
  }
}
